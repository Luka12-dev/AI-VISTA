use anyhow::{Context, Result};
use clap::Parser;
use crossbeam_channel::{bounded, Receiver, Sender};
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use memmap2::MmapOptions;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use walkdir::WalkDir;

#[cfg(unix)]
use libc;

#[cfg(feature = "gpu")]
mod gpu {
    use anyhow::{Context, Result};
    use ocl::{flags, Buffer, Kernel, Platform, ProQue};
    use std::path::Path;

    // Small non-cryptographic GPU XOR kernel that reduces u64 chunks to a single u64.
    // NOTE: This is just to stress GPU memory transfer and compute.
    const KERNEL_SRC: &str = r#"
        __kernel void xor_reduce(__global const ulong* data, __global ulong* out, uint n) {
            uint gid = get_global_id(0);
            ulong acc = 0;
            // stride loop for safety
            for (uint i = gid; i < n; i += get_global_size(0)) {
                acc ^= data[i];
            }
            // each work-item writes its partial result into out[gid], host will reduce it
            out[gid] = acc;
        }
    "#;

    pub struct GpuContext {
        pro_que: ProQue,
        max_work_items: usize,
    }

    impl GpuContext {
        pub fn try_new() -> Result<Self> {
            // Create a ProQue on the first available platform/device
            let platform = Platform::default();
            let pro_que = ProQue::builder()
                .platform(platform)
                .src(KERNEL_SRC)
                .build()
                .context("Failed to build OpenCL ProQue")?;
            // max work items = device max compute units * some multiplier, clamp
            let device = pro_que.device();
            let max_wi = device.max_work_group_size()? as usize;
            let max_items = (device.max_compute_units()? as usize) * max_wi;
            Ok(Self {
                pro_que,
                max_work_items: max_items.clamp(64, 4096),
            })
        }

        /// Compute an XOR64 reduction on the provided bytes using the GPU.
        /// We will pad/truncate to u64 multiples and copy to GPU in chunks to avoid OOM.
        pub fn xor64_for_file(&self, bytes: &[u8]) -> Result<u64> {
            // Build a u64 slice view (pad if necessary)
            let mut len_u64 = bytes.len() / 8;
            if bytes.len() % 8 != 0 {
                len_u64 += 1;
            }
            // Prepare a Vec<u64> with zero padding
            let mut u64buf = vec![0u64; len_u64];
            let mut rdr = bytes;
            for i in 0..len_u64 {
                let mut chunk = [0u8; 8];
                let take = std::cmp::min(8, rdr.len());
                chunk[..take].copy_from_slice(&rdr[..take]);
                u64buf[i] = u64::from_le_bytes(chunk);
                if rdr.len() <= take {
                    break;
                }
                rdr = &rdr[take..];
            }

            // Create buffers and run kernel in one shot
            let n = u64buf.len();
            let wg = std::cmp::min(self.max_work_items, n);
            let in_buf = Buffer::<u64>::builder()
                .queue(self.pro_que.queue().clone())
                .flags(flags::MEM_READ_ONLY)
                .len(n)
                .copy_host_slice(&u64buf)
                .build()
                .context("Failed to build input buffer")?;
            let out_buf = Buffer::<u64>::builder()
                .queue(self.pro_que.queue().clone())
                .flags(flags::MEM_WRITE_ONLY)
                .len(wg)
                .build()
                .context("Failed to build output buffer")?;

            let kernel = Kernel::builder()
                .program(self.pro_que.program())
                .name("xor_reduce")
                .global_work_size(wg)
                .arg(&in_buf)
                .arg(&out_buf)
                .arg(n as u32)
                .queue(self.pro_que.queue().clone())
                .build()
                .context("Failed to build kernel")?;

            unsafe {
                kernel.enq().context("Failed to enqueue kernel")?;
            }

            // Read partial results and reduce on host
            let mut partials = vec![0u64; wg];
            out_buf
                .read(&mut partials)
                .enq()
                .context("Failed to read partials")?;
            let mut acc = 0u64;
            for v in partials {
                acc ^= v;
            }
            Ok(acc)
        }
    }
}

#[derive(Parser)]
struct Args {
    /// Path to cache directory
    #[clap(short, long, default_value = "model_cache")]
    cache: PathBuf,

    /// Number of parallel worker threads (defaults to number of physical cores)
    #[clap(short = 'j', long)]
    jobs: Option<usize>,

    /// Enable GPU XOR warmup/checksum (requires --features gpu)
    #[clap(long)]
    gpu: bool,

    /// Only warm files (mmap + advise + optional hashing), don't compute heavy stats
    #[clap(long)]
    warm_only: bool,

    /// Limit processing to files larger than this many bytes (default 0)
    #[clap(long, default_value_t = 0)]
    min_bytes: u64,
}

#[derive(Debug)]
struct FileReport {
    path: PathBuf,
    size: u64,
    blake3_hex: Option<String>,
    xor64_gpu: Option<u64>,
    elapsed_ms: u128,
}

fn physical_cpus() -> usize {
    num_cpus::get_physical().max(1)
}

/// Try to advise OS to prefetch the mapped region (POSIX madvise MADV_WILLNEED where supported)
#[inline]
fn advise_willneed(ptr: *const u8, len: usize) {
    #[cfg(unix)]
    unsafe {
        // madvise tends to be available on Linux/BSD/macOS (value MADV_WILLNEED)
        let res = libc::madvise(ptr as *mut _, len, libc::MADV_WILLNEED);
        if res != 0 {
            // ignore errors (best-effort)
        }
    }
    // On Windows and others we do nothing (memmap still helps).
}

/// Process a single file: mmap, advise, compute blake3, optional gpu xor.
/// Returns a FileReport.
fn process_file(
    path: &Path,
    min_bytes: u64,
    use_gpu: bool,
    gpu_ctx: Option<&gpu::GpuContext>,
) -> anyhow::Result<FileReport> {
    let start = Instant::now();
    let meta = path.metadata()?;
    let size = meta.len();
    if size < min_bytes {
        let elapsed = start.elapsed().as_millis();
        return Ok(FileReport {
            path: path.to_path_buf(),
            size,
            blake3_hex: None,
            xor64_gpu: None,
            elapsed_ms: elapsed,
        });
    }

    // open file readonly
    let f = File::open(path)?;
    // memory-map entire file read-only (safe cross-platform)
    let mmap = unsafe { MmapOptions::new().map(&f) }?;
    let data = &mmap[..];

    // advise OS to prefetch (best-effort)
    advise_willneed(data.as_ptr(), data.len());

    // Compute blake3 hash (super-fast, SIMD, streaming)
    // For large maps, hashing the slice directly is fine.
    let blake3_hex = {
        // Use streaming hasher for consistency and small memory overhead
        let mut hasher = blake3::Hasher::new();
        hasher.update(data);
        let hash = hasher.finalize();
        Some(hash.to_hex().to_string())
    };

    // GPU optional quick XOR checksum (non-cryptographic)
    let xor64_gpu = if use_gpu {
        match gpu_ctx {
            Some(ctx) => match ctx.xor64_for_file(data) {
                Ok(v) => Some(v),
                Err(_) => None,
            },
            None => None,
        }
    } else {
        None
    };

    let elapsed = start.elapsed().as_millis();
    Ok(FileReport {
        path: path.to_path_buf(),
        size,
        blake3_hex,
        xor64_gpu,
        elapsed_ms: elapsed,
    })
}

fn human_bytes(bytes: u128) -> String {
    const UNITS: [&str; 6] = ["B", "KB", "MB", "GB", "TB", "PB"];
    let mut b = bytes as f64;
    let mut i = 0;
    while b >= 1024.0 && i < UNITS.len() - 1 {
        b /= 1024.0;
        i += 1;
    }
    format!("{:.2} {}", b, UNITS[i])
}

fn main() -> Result<()> {
    let args = Args::parse();

    let start_all = Instant::now();

    if !args.cache.exists() {
        anyhow::bail!("Cache path {:?} does not exist", args.cache);
    }

    // Determine number of threads
    let num_workers = args
        .jobs
        .unwrap_or_else(|| physical_cpus().saturating_mul(1)); // 1x physical cores
    rayon::ThreadPoolBuilder::new()
        .num_threads(num_workers)
        .build_global()
        .context("Failed to initialize rayon thread pool")?;

    println!(
        "Scanning cache: {:?}  (workers={})",
        args.cache, num_workers
    );

    // Gather files first (cheap), then parallel process with progress bar
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in WalkDir::new(&args.cache).into_iter().filter_map(|e| e.ok()) {
        if entry.file_type().is_file() {
            files.push(entry.into_path());
        }
    }

    files.sort(); // deterministic order

    let total_files = files.len();
    let total_bytes_est: u128 = files
        .iter()
        .filter_map(|p| p.metadata().ok().map(|m| m.len() as u128))
        .sum();

    println!(
        "Found {} files, ~{} total.",
        total_files,
        human_bytes(total_bytes_est)
    );

    // Possibly initialize GPU context
    #[cfg(feature = "gpu")]
    let gpu_ctx = if args.gpu {
        match gpu::GpuContext::try_new() {
            Ok(ctx) => {
                println!("[GPU] OpenCL GPU context available. GPU warmup enabled.");
                Some(Arc::new(ctx))
            }
            Err(e) => {
                println!("[GPU] OpenCL init failed (falling back to CPU only): {:?}", e);
                None
            }
        }
    } else {
        None
    };
    #[cfg(not(feature = "gpu"))]
    let gpu_ctx: Option<Arc<()>> = None;

    // Prepare multi-progress bars
    let m = MultiProgress::new();
    let pb_files = m.add(ProgressBar::new(total_files as u64));
    pb_files.set_style(
        ProgressStyle::with_template("{spinner:.green} [{elapsed_precise}] {bar:40.cyan/blue} {pos}/{len} files")
            .unwrap()
            .progress_chars("#>-"),
    );

    let pb_bytes = m.add(ProgressBar::new(total_bytes_est as u64));
    pb_bytes.set_style(
        ProgressStyle::with_template("{msg} {bytes:>7}/{total_bytes:7} {eta_precise}")
            .unwrap()
            .progress_chars("=>-"),
    );
    pb_bytes.set_message("scanned bytes:");

    // Channels for results aggregation
    let (tx, rx): (Sender<FileReport>, Receiver<FileReport>) = bounded(1024);

    // Atomic counters
    let total_processed = Arc::new(AtomicU64::new(0));
    let total_bytes_processed = Arc::new(AtomicU64::new(0));

    // Start a background aggregator thread to collect results and update progress bars
    let agg_total_files = total_files;
    let agg_total_bytes = total_bytes_est as u64;
    let agg_handle = {
        let pb_files = pb_files.clone();
        let pb_bytes = pb_bytes.clone();
        let total_processed = Arc::clone(&total_processed);
        let total_bytes_processed = Arc::clone(&total_bytes_processed);
        std::thread::spawn(move || {
            let mut reports: Vec<FileReport> = Vec::with_capacity(agg_total_files.min(1000));
            let mut largest: Vec<(u64, PathBuf)> = Vec::new();
            while let Ok(rep) = rx.recv() {
                // update counters
                total_processed.fetch_add(1, Ordering::Relaxed);
                total_bytes_processed.fetch_add(rep.size as u64, Ordering::Relaxed);

                // update PBs
                pb_files.inc(1);
                pb_bytes.inc(rep.size);

                // keep some aggregated info
                largest.push((rep.size, rep.path.clone()));
                if largest.len() > 256 {
                    // keep top 256 largest
                    largest.sort_by_key(|(s, _p)| Reverse(*s));
                    largest.truncate(256);
                }

                reports.push(rep);
            }

            // finalize
            pb_files.finish_with_message("files processed");
            pb_bytes.finish_with_message("bytes processed");
            reports.sort_by_key(|r| Reverse(r.size));
            // assemble a short summary
            let total_files = reports.len();
            let total_bytes: u128 = reports.iter().map(|r| r.size as u128).sum();
            println!("\n--- Summary ---");
            println!("Processed files: {}", total_files);
            println!("Total bytes processed: {}", human_bytes(total_bytes));
            if !reports.is_empty() {
                println!("\nTop 10 largest files:");
                for r in reports.iter().take(10) {
                    println!(
                        "  {:>8}  {}",
                        human_bytes(r.size as u128),
                        r.path.display()
                    );
                }
            }
            // return reports via thread results? we'll just print summary here.
        })
    };

    // Kick off parallel processing using rayon parallel iterator but send results to aggregator channel
    let tx_arc = Arc::new(tx);
    let use_gpu_flag = args.gpu;
    let min_bytes = args.min_bytes;
    let warm_only = args.warm_only;

    // Parallel iterate over files in chunks to avoid overwhelming rayon with channel ops
    files.par_chunks(128).for_each(|chunk| {
        // chunk processed on this thread
        // Prepare optional gpu context clone for this thread
        #[cfg(feature = "gpu")]
        let local_gpu = gpu_ctx.clone();

        for p in chunk {
            // process file with best-effort error handling
            match (|| -> Result<FileReport> {
                #[cfg(feature = "gpu")]
                {
                    let gpu_ref = local_gpu.as_ref().and_then(|a| a.as_ref());
                    // convert Arc<gpu::GpuContext> to Option<&gpu::GpuContext> for passing
                    let gpu_ctx_ref = gpu_ref.map(|arc_ctx| &**arc_ctx);
                    process_file(p, min_bytes, use_gpu_flag, gpu_ctx_ref)
                        .with_context(|| format!("processing file {:?}", p))
                }
                #[cfg(not(feature = "gpu"))]
                {
                    let _ = &use_gpu_flag; // unused
                    process_file(p, min_bytes, false, None)
                        .with_context(|| format!("processing file {:?}", p))
                }
            }) {
                Ok(report) => {
                    let _ = tx_arc.send(report);
                }
                Err(e) => {
                    // send a minimal report for error, still count file as processed
                    let err_report = FileReport {
                        path: p.clone(),
                        size: p.metadata().map(|m| m.len()).unwrap_or(0),
                        blake3_hex: None,
                        xor64_gpu: None,
                        elapsed_ms: 0,
                    };
                    let _ = tx_arc.send(err_report);
                    eprintln!("[WARN] Error processing {:?}: {:?}", p, e);
                }
            }
        }
    });

    // drop sender so aggregator sees end
    drop(tx_arc);

    // Wait for aggregator to finish. In this design, aggregator thread listens until rx closed.
    agg_handle.join().unwrap();

    let elapsed = start_all.elapsed();
    println!(
        "\nAll done in {:.2}s (wall).",
        elapsed.as_secs_f64()
    );
    Ok(())
}