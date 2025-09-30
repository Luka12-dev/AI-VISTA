
    def event_generator():
        yield "data: " + json.dumps({"type":"sse_open"}) + "\n\n"
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            # item should already be a JSON string; ensure it's safe
            try:
                if isinstance(item, str):
                    yield f"data: {item}\n\n"
                else:
                    # fallback: convert to json
                    yield "data: " + json.dumps(item) + "\n\n"
            except Exception:
                # if even this fails, send generic error and continue
                try:
                    yield "data: " + json.dumps({"type":"error","text":"Internal serialization error"}) + "\n\n"
                except Exception:
                    pass
        yield "data: " + json.dumps({"type":"sse_closed"}) + "\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

@app.get("/api/generate/stream")
async def api_generate_stream_get(payload: Optional[str] = None):
    if not payload:
        return JSONResponse({"ok": False, "msg": "provide 'payload' url-encoded JSON string"}, status_code=400)
    try:
        payload_json = json.loads(payload)
    except Exception:
        return JSONResponse({"ok": False, "msg": "Invalid payload (must be JSON string)"}, status_code=400)
    return start_generation_and_stream(payload_json)

@app.post("/api/generate/stream")
async def api_generate_stream_post(req: Request):
    try:
        payload_json = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "Invalid JSON body"}, status_code=400)
    return start_generation_and_stream(payload_json)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def main():
    print("Choose server mode: 1 = local-only (127.0.0.1), 2 = multi (0.0.0.0)")
    choice = input("Mode [1/2]: ").strip()
    if choice == "2":
        host = "0.0.0.0"
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
        app.state.server_mode = "multi"
    else:
        host = "127.0.0.1"
        app.state.server_mode = "local"
    port = 8000
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = get_local_ip()
    lan_url = f"http://{lan_ip}:{port}"
    print(f"Starting FastAPI server on {host}:{port} (mode={app.state.server_mode})")
    print("Open in browser:")
    print(f" - Local: {local_url}")
    if host == "0.0.0.0":
        print(f" - LAN:   {lan_url}")
    if not ML_AVAILABLE:
        print("[WARN] ML libraries missing. Generation endpoints will return errors.")
        print("ML import error:", ML_IMPORT_ERROR)
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()