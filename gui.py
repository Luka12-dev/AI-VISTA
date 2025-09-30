import os
import shutil
from pathlib import Path
from datetime import datetime
import subprocess
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QUrl, QTimer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit, QProgressBar,
    QApplication, QFileDialog, QDialog, QFormLayout, QDialogButtonBox, QMessageBox
)
from PyQt6.QtGui import QFont, QDesktopServices
from PyQt6.QtWidgets import QGraphicsOpacityEffect

from loader import ModelDownloaderThread
from generator import ImageGeneratorThread
from settings.settings import ResourceController, Settings

PROJECT_ROOT = Path(__file__).parent
CACHE_DIR = PROJECT_ROOT / 'model_cache'
IMAGE_DIR = PROJECT_ROOT / 'generated_images'
CACHE_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(exist_ok=True)

class RustOptimizerThread(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, rust_path: str):
        super().__init__()
        self.rust_path = rust_path
        self._stop_requested = False

    def run(self):
        try:
            self.log.emit("[RUST] Starting Rust optimizer...")
            process = subprocess.Popen(
                ["cargo", "run", "--release", "--manifest-path",
                 str(PROJECT_ROOT / "rust" / "optimizer" / "Cargo.toml")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            while True:
                if self._stop_requested:
                    process.terminate()
                    self.log.emit("[RUST] Rust optimizer terminated by user.")
                    self.finished.emit(False, "Terminated")
                    return

                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    self.log.emit(f"[RUST] {line.strip()}")

            retcode = process.poll()
            if retcode == 0:
                self.finished.emit(True, "Rust optimization finished successfully")
            else:
                self.finished.emit(False, f"Rust exited with code {retcode}")

        except Exception as e:
            self.finished.emit(False, str(e))
            self.log.emit(f"[RUST ERROR] {e}")

    def stop(self):
        self._stop_requested = True

TRANSLATIONS = {
    'en': {
        'title': 'AIVISTA Pro - Image Studio',
        'model': 'Model:',
        'token_placeholder': 'HF token (optional)',
        'download': 'Download / Ensure',
        'download_start': 'Starting download for {model}...',
        'download_finished': 'Download finished: {msg}',
        'download_failed': 'Download failed: {msg}',
        'prompt_placeholder': 'Enter prompt (e.g. futuristic city at dusk, highly detailed)',
        'generate': 'Generate Image',
        'prompt_empty': 'Prompt empty - enter something.',
        'select_output': 'Choose Output Folder',
        'output_selected': 'Output folder set to: {path}',
        'cancel_download': 'Cancel Download',
        'device': 'Device:',
        'precision': 'Precision:',
        'scheduler': 'Scheduler:',
        'language': 'Language:',
        'preview': 'Preview Latest',
        'open_output': 'Open Output Folder',
        'clear_cache': 'Clear Model Cache',
        'cache_cleared': 'Model cache cleared.',
        'no_images': 'No generated images found.',
        'preview_failed': 'Preview failed: {msg}',
        'settings': 'Settings',
        'settings_title': 'Settings',
        'settings_saved': 'Settings saved and applied.',
    },

    'sr_lat': {
        'title': 'AIVISTA Pro - Studio Slika',
        'model': 'Model:',
        'token_placeholder': 'HF token (opciono)',
        'download': 'Skini / Proveri',
        'download_start': 'Počinje skidanje modela {model}...',
        'download_finished': 'Skidanje završeno: {msg}',
        'download_failed': 'Skidanje nije uspelo: {msg}',
        'prompt_placeholder': 'Unesi prompt (npr. futuristički grad u suton, detaljno)',
        'generate': 'Generiši Sliku',
        'prompt_empty': 'Prompt je prazan - unesite nešto.',
        'select_output': 'Izaberi izlazni folder',
        'output_selected': 'Izlazni folder: {path}',
        'cancel_download': 'Otkaži skidanje',
        'device': 'Uređaj:',
        'precision': 'Preciznost:',
        'scheduler': 'Scheduler:',
        'language': 'Jezik:',
        'preview': 'Pregledaj poslednju',
        'open_output': 'Otvori izlazni folder',
        'clear_cache': 'Očisti keš modela',
        'cache_cleared': 'Keš modela očišćen.',
        'no_images': 'Nema generisanih slika.',
        'preview_failed': 'Pregled neuspeo: {msg}',
        'settings': 'Podešavanja',
        'settings_title': 'Podešavanja',
        'settings_saved': 'Podešavanja sačuvana i primenjena.',
    },

    'sr_cyr': {
        'title': 'AIVISTA Pro - Студио Слика',
        'model': 'Модел:',
        'token_placeholder': 'HF токен (опционо)',
        'download': 'Скини / Провери',
        'download_start': 'Почиње скидање модела {model}...',
        'download_finished': 'Скидање завршено: {msg}',
        'download_failed': 'Скидање није успело: {msg}',
        'prompt_placeholder': 'Унеси промпт (нпр. футуристички град у сумрак, детаљно)',
        'generate': 'Генериши Слику',
        'prompt_empty': 'Промпт је празан - унесите нешто.',
        'select_output': 'Одабери фолдер за излаз',
        'output_selected': 'Излазни фолдер: {path}',
        'cancel_download': 'Откажи скидање',
        'device': 'Уређај:',
        'precision': 'Прецизност:',
        'scheduler': 'Scheduler:',
        'language': 'Језик:',
        'preview': 'Прегледај последњу',
        'open_output': 'Отвори излазни фолдер',
        'clear_cache': 'Обриши кеш модела',
        'cache_cleared': 'Кеш модела обрисан.',
        'no_images': 'Нема генерисаних слика.',
        'preview_failed': 'Преглед није успео: {msg}',
        'settings': 'Подешавања',
        'settings_title': 'Подешавања',
        'settings_saved': 'Подешавања сачувана и примењена.',
    },

    'es': {
        'title': 'AIVISTA Pro - Estudio de Imagen',
        'model': 'Modelo:',
        'token_placeholder': 'Token HF (opcional)',
        'download': 'Descargar / Asegurar',
        'download_start': 'Iniciando descarga del modelo {model}...',
        'download_finished': 'Descarga finalizada: {msg}',
        'download_failed': 'Descarga fallida: {msg}',
        'prompt_placeholder': 'Introduce prompt (ej. ciudad futurista al atardecer, muy detallado)',
        'generate': 'Generar Imagen',
        'prompt_empty': 'Prompt vacío - introduce algo.',
        'select_output': 'Elegir carpeta de salida',
        'output_selected': 'Carpeta de salida: {path}',
        'cancel_download': 'Cancelar descarga',
        'device': 'Dispositivo:',
        'precision': 'Precisión:',
        'scheduler': 'Scheduler:',
        'language': 'Idioma:',
        'preview': 'Previsualizar última',
        'open_output': 'Abrir carpeta de salida',
        'clear_cache': 'Limpiar caché de modelos',
        'cache_cleared': 'Caché de modelos limpiado.',
        'no_images': 'No se encontraron imágenes generadas.',
        'preview_failed': 'Previsualización fallida: {msg}',
        'settings': 'Ajustes',
        'settings_title': 'Ajustes',
        'settings_saved': 'Ajustes guardados y aplicados.',
    },

    'ru': {
        'title': 'AIVISTA Pro - Студия Изображений',
        'model': 'Модель:',
        'token_placeholder': 'HF токен (необязательно)',
        'download': 'Скачать / Проверить',
        'download_start': 'Начинается загрузка модели {model}...',
        'download_finished': 'Загрузка завершена: {msg}',
        'download_failed': 'Ошибка загрузки: {msg}',
        'prompt_placeholder': 'Введите prompt (напр. футуристический город на закате, очень детализированно)',
        'generate': 'Сгенерировать изображение',
        'prompt_empty': 'Prompt пуст - введите что-то.',
        'select_output': 'Выбрать папку для вывода',
        'output_selected': 'Папка вывода: {path}',
        'cancel_download': 'Отменить загрузку',
        'device': 'Устройство:',
        'precision': 'Точность:',
        'scheduler': 'Scheduler:',
        'language': 'Язык:',
        'preview': 'Предпросмотр последней',
        'open_output': 'Открыть папку вывода',
        'clear_cache': 'Очистить кэш моделей',
        'cache_cleared': 'Кэш моделей очищен.',
        'no_images': 'Генерированных изображений не найдено.',
        'preview_failed': 'Не удалось открыть превью: {msg}',
        'settings': 'Настройки',
        'settings_title': 'Настройки',
        'settings_saved': 'Настройки сохранены и применены.',
    },

    'fr': {
        'title': 'AIVISTA Pro - Studio d\'Images',
        'model': 'Modèle :',
        'token_placeholder': 'Jeton HF (optionnel)',
        'download': 'Télécharger / Vérifier',
        'download_start': 'Début du téléchargement du modèle {model}...',
        'download_finished': 'Téléchargement terminé : {msg}',
        'download_failed': 'Échec du téléchargement : {msg}',
        'prompt_placeholder': 'Entrez le prompt (ex. ville futuriste au crépuscule, très détaillé)',
        'generate': 'Générer l\'image',
        'prompt_empty': 'Prompt vide - entrez quelque chose.',
        'select_output': 'Choisir le dossier de sortie',
        'output_selected': 'Dossier de sortie défini : {path}',
        'cancel_download': 'Annuler le téléchargement',
        'device': 'Périphérique :',
        'precision': 'Précision :',
        'scheduler': 'Scheduler :',
        'language': 'Langue :',
        'preview': 'Prévisualiser le dernier',
        'open_output': 'Ouvrir le dossier de sortie',
        'clear_cache': 'Vider le cache des modèles',
        'cache_cleared': 'Cache des modèles vidé.',
        'no_images': 'Aucune image générée trouvée.',
        'preview_failed': 'Échec de la prévisualisation : {msg}',
        'settings': 'Paramètres',
        'settings_title': 'Paramètres',
        'settings_saved': 'Paramètres enregistrés et appliqués.',
    },

    'de': {
        'title': 'AIVISTA Pro - Bildstudio',
        'model': 'Modell:',
        'token_placeholder': 'HF-Token (optional)',
        'download': 'Herunterladen / Sicherstellen',
        'download_start': 'Starte Download für {model}...',
        'download_finished': 'Download beendet: {msg}',
        'download_failed': 'Download fehlgeschlagen: {msg}',
        'prompt_placeholder': 'Prompt eingeben (z. B. futuristische Stadt bei Dämmerung, sehr detailliert)',
        'generate': 'Bild generieren',
        'prompt_empty': 'Prompt leer - bitte etwas eingeben.',
        'select_output': 'Ausgabeordner wählen',
        'output_selected': 'Ausgabeordner gesetzt: {path}',
        'cancel_download': 'Download abbrechen',
        'device': 'Gerät:',
        'precision': 'Präzision:',
        'scheduler': 'Scheduler:',
        'language': 'Sprache:',
        'preview': 'Letzte Vorschau',
        'open_output': 'Ausgabeordner öffnen',
        'clear_cache': 'Modell-Cache leeren',
        'cache_cleared': 'Modell-Cache geleert.',
        'no_images': 'Keine generierten Bilder gefunden.',
        'preview_failed': 'Vorschau fehlgeschlagen: {msg}',
        'settings': 'Einstellungen',
        'settings_title': 'Einstellungen',
        'settings_saved': 'Einstellungen gespeichert und angewendet.',
    },

    'it': {
        'title': 'AIVISTA Pro - Studio Immagini',
        'model': 'Modello:',
        'token_placeholder': 'Token HF (opzionale)',
        'download': 'Scarica / Assicura',
        'download_start': 'Avvio download modello {model}...',
        'download_finished': 'Download terminato: {msg}',
        'download_failed': 'Download fallito: {msg}',
        'prompt_placeholder': 'Inserisci prompt (es. città futuristica al tramonto, molto dettagliata)',
        'generate': 'Genera Immagine',
        'prompt_empty': 'Prompt vuoto - inserisci qualcosa.',
        'select_output': 'Scegli cartella di output',
        'output_selected': 'Cartella di output: {path}',
        'cancel_download': 'Annulla download',
        'device': 'Dispositivo:',
        'precision': 'Precisione:',
        'scheduler': 'Scheduler:',
        'language': 'Lingua:',
        'preview': 'Anteprima ultima',
        'open_output': 'Apri cartella di output',
        'clear_cache': 'Pulisci cache modelli',
        'cache_cleared': 'Cache modelli ripulita.',
        'no_images': 'Nessuna immagine generata trovata.',
        'preview_failed': 'Anteprima fallita: {msg}',
        'settings': 'Impostazioni',
        'settings_title': 'Impostazioni',
        'settings_saved': 'Impostazioni salvate e applicate.',
    },

    'pt': {
        'title': 'AIVISTA Pro - Estúdio de Imagem',
        'model': 'Modelo:',
        'token_placeholder': 'Token HF (opcional)',
        'download': 'Baixar / Garantir',
        'download_start': 'Iniciando download do modelo {model}...',
        'download_finished': 'Download finalizado: {msg}',
        'download_failed': 'Falha no download: {msg}',
        'prompt_placeholder': 'Digite o prompt (ex. cidade futurista ao entardecer, muito detalhada)',
        'generate': 'Gerar Imagem',
        'prompt_empty': 'Prompt vazio - insira algo.',
        'select_output': 'Escolher pasta de saída',
        'output_selected': 'Pasta de saída: {path}',
        'cancel_download': 'Cancelar download',
        'device': 'Dispositivo:',
        'precision': 'Precisão:',
        'scheduler': 'Scheduler:',
        'language': 'Idioma:',
        'preview': 'Visualizar última',
        'open_output': 'Abrir pasta de saída',
        'clear_cache': 'Limpar cache de modelos',
        'cache_cleared': 'Cache de modelos limpo.',
        'no_images': 'Nenhuma imagem gerada encontrada.',
        'preview_failed': 'Falha na visualização: {msg}',
        'settings': 'Configurações',
        'settings_title': 'Configurações',
        'settings_saved': 'Configurações salvas e aplicadas.',
    },

    'nl': {
        'title': 'AIVISTA Pro - Beeldstudio',
        'model': 'Model:',
        'token_placeholder': 'HF-token (optioneel)',
        'download': 'Download / Controleren',
        'download_start': 'Start downloaden van {model}...',
        'download_finished': 'Download voltooid: {msg}',
        'download_failed': 'Download mislukt: {msg}',
        'prompt_placeholder': 'Voer prompt in (bv. futuristische stad bij schemering, zeer gedetailleerd)',
        'generate': 'Afbeelding genereren',
        'prompt_empty': 'Prompt leeg - voer iets in.',
        'select_output': 'Kies uitvoermap',
        'output_selected': 'Uitvoermap ingesteld: {path}',
        'cancel_download': 'Download annuleren',
        'device': 'Apparaat:',
        'precision': 'Precisie:',
        'scheduler': 'Scheduler:',
        'language': 'Taal:',
        'preview': 'Laatste preview',
        'open_output': 'Open uitvoermap',
        'clear_cache': 'Model cache wissen',
        'cache_cleared': 'Model cache gewist.',
        'no_images': 'Geen gegenereerde afbeeldingen gevonden.',
        'preview_failed': 'Preview mislukt: {msg}',
        'settings': 'Instellingen',
        'settings_title': 'Instellingen',
        'settings_saved': 'Instellingen opgeslagen en toegepast.',
    },

    'sv': {
        'title': 'AIVISTA Pro - Bildstudio',
        'model': 'Modell:',
        'token_placeholder': 'HF-token (valfritt)',
        'download': 'Ladda ner / Säkerställ',
        'download_start': 'Startar nedladdning för {model}...',
        'download_finished': 'Nedladdning klar: {msg}',
        'download_failed': 'Nedladdning misslyckades: {msg}',
        'prompt_placeholder': 'Ange prompt (t.ex. futuristisk stad vid skymning, mycket detaljerad)',
        'generate': 'Generera bild',
        'prompt_empty': 'Prompt tom - skriv in något.',
        'select_output': 'Välj utmatningsmapp',
        'output_selected': 'Utmatningsmapp satt till: {path}',
        'cancel_download': 'Avbryt nedladdning',
        'device': 'Enhet:',
        'precision': 'Precision:',
        'scheduler': 'Scheduler:',
        'language': 'Språk:',
        'preview': 'Förhandsgranska senaste',
        'open_output': 'Öppna utmatningsmapp',
        'clear_cache': 'Rensa modellcache',
        'cache_cleared': 'Modellcache rensad.',
        'no_images': 'Inga genererade bilder hittades.',
        'preview_failed': 'Förhandsgranskning misslyckades: {msg}',
        'settings': 'Inställningar',
        'settings_title': 'Inställningar',
        'settings_saved': 'Inställningar sparade och tillämpade.',
    },

    'pl': {
        'title': 'AIVISTA Pro - Studio Obrazów',
        'model': 'Model:',
        'token_placeholder': 'Token HF (opcjonalnie)',
        'download': 'Pobierz / Sprawdź',
        'download_start': 'Rozpoczynanie pobierania modelu {model}...',
        'download_finished': 'Pobieranie zakończone: {msg}',
        'download_failed': 'Pobieranie nie powiodło się: {msg}',
        'prompt_placeholder': 'Wprowadź prompt (np. futurystyczne miasto o zmierzchu, bardzo szczegółowe)',
        'generate': 'Generuj obraz',
        'prompt_empty': 'Prompt pusty - wpisz coś.',
        'select_output': 'Wybierz folder wyjściowy',
        'output_selected': 'Folder wyjściowy ustawiony: {path}',
        'cancel_download': 'Anuluj pobieranie',
        'device': 'Urządzenie:',
        'precision': 'Precyzja:',
        'scheduler': 'Scheduler:',
        'language': 'Język:',
        'preview': 'Podgląd ostatniego',
        'open_output': 'Otwórz folder wyjściowy',
        'clear_cache': 'Wyczyść pamięć podręczną modeli',
        'cache_cleared': 'Pamięć podręczna modeli wyczyszczona.',
        'no_images': 'Nie znaleziono wygenerowanych obrazów.',
        'preview_failed': 'Podgląd nie powiódł się: {msg}',
        'settings': 'Ustawienia',
        'settings_title': 'Ustawienia',
        'settings_saved': 'Ustawienia zapisane i zastosowane.',
    },

    'tr': {
        'title': 'AIVISTA Pro - Görüntü Stüdyosu',
        'model': 'Model:',
        'token_placeholder': 'HF token (isteğe bağlı)',
        'download': 'İndir / Sağla',
        'download_start': '{model} için indirme başlatılıyor...',
        'download_finished': 'İndirme tamamlandı: {msg}',
        'download_failed': 'İndirme başarısız: {msg}',
        'prompt_placeholder': 'Prompt girin (örn. gün batımında futuristik şehir, çok detaylı)',
        'generate': 'Görüntü Oluştur',
        'prompt_empty': 'Prompt boş - lütfen bir şey girin.',
        'select_output': 'Çıkış Klasörü Seç',
        'output_selected': 'Çıkış klasörü ayarlandı: {path}',
        'cancel_download': 'İndirmeyi İptal Et',
        'device': 'Aygıt:',
        'precision': 'Hassasiyet:',
        'scheduler': 'Scheduler:',
        'language': 'Dil:',
        'preview': 'Sonuncuyu Önizle',
        'open_output': 'Çıkış klasörünü aç',
        'clear_cache': 'Model önbelleğini temizle',
        'cache_cleared': 'Model önbelleği temizlendi.',
        'no_images': 'Üretilmiş resim bulunamadı.',
        'preview_failed': 'Önizleme başarısız: {msg}',
        'settings': 'Ayarlar',
        'settings_title': 'Ayarlar',
        'settings_saved': 'Ayarlar kaydedildi ve uygulandı.',
    },

    'ja': {
        'title': 'AIVISTA Pro - 画像スタジオ',
        'model': 'モデル:',
        'token_placeholder': 'HFトークン（任意）',
        'download': 'ダウンロード / 確認',
        'download_start': '{model} のダウンロードを開始します...',
        'download_finished': 'ダウンロード完了: {msg}',
        'download_failed': 'ダウンロードに失敗しました: {msg}',
        'prompt_placeholder': 'プロンプトを入力してください（例：夕暮れの未来都市、非常に詳細）',
        'generate': '画像を生成',
        'prompt_empty': 'プロンプトが空です - 何か入力してください。',
        'select_output': '出力フォルダを選択',
        'output_selected': '出力フォルダ: {path}',
        'cancel_download': 'ダウンロードをキャンセル',
        'device': 'デバイス:',
        'precision': '精度:',
        'scheduler': 'Scheduler:',
        'language': '言語:',
        'preview': '最新をプレビュー',
        'open_output': '出力フォルダを開く',
        'clear_cache': 'モデルキャッシュをクリア',
        'cache_cleared': 'モデルキャッシュをクリアしました。',
        'no_images': '生成された画像が見つかりません。',
        'preview_failed': 'プレビューに失敗しました: {msg}',
        'settings': '設定',
        'settings_title': '設定',
        'settings_saved': '設定を保存して適用しました。',
    },

    'zh_cn': {
        'title': 'AIVISTA Pro - 图像工作室',
        'model': '模型:',
        'token_placeholder': 'HF 令牌（可选）',
        'download': '下载 / 确保',
        'download_start': '开始下载模型 {model}...',
        'download_finished': '下载完成: {msg}',
        'download_failed': '下载失败: {msg}',
        'prompt_placeholder': '输入提示（例如：黄昏的未来城市，高度细节）',
        'generate': '生成图像',
        'prompt_empty': '提示为空 - 请输入内容。',
        'select_output': '选择输出文件夹',
        'output_selected': '输出文件夹: {path}',
        'cancel_download': '取消下载',
        'device': '设备:',
        'precision': '精度:',
        'scheduler': 'Scheduler:',
        'language': '语言:',
        'preview': '预览最新',
        'open_output': '打开输出文件夹',
        'clear_cache': '清除模型缓存',
        'cache_cleared': '模型缓存已清除。',
        'no_images': '未找到生成的图像。',
        'preview_failed': '预览失败: {msg}',
        'settings': '设置',
        'settings_title': '设置',
        'settings_saved': '设置已保存并应用。',
    },

    'zh_tw': {
        'title': 'AIVISTA Pro - 影像工作室',
        'model': '模型:',
        'token_placeholder': 'HF 令牌（可選）',
        'download': '下載 / 確保',
        'download_start': '開始下載模型 {model}...',
        'download_finished': '下載完成: {msg}',
        'download_failed': '下載失敗: {msg}',
        'prompt_placeholder': '輸入提示（例：黃昏的未來城市，高度細節）',
        'generate': '生成圖像',
        'prompt_empty': '提示為空 - 請輸入內容。',
        'select_output': '選擇輸出資料夾',
        'output_selected': '輸出資料夾: {path}',
        'cancel_download': '取消下載',
        'device': '裝置:',
        'precision': '精度:',
        'scheduler': 'Scheduler:',
        'language': '語言:',
        'preview': '預覽最新',
        'open_output': '打開輸出資料夾',
        'clear_cache': '清除模型快取',
        'cache_cleared': '模型快取已清除。',
        'no_images': '未找到生成的圖像。',
        'preview_failed': '預覽失敗: {msg}',
        'settings': '設定',
        'settings_title': '設定',
        'settings_saved': '設定已保存並應用。',
    },

    'ko': {
        'title': 'AIVISTA Pro - 이미지 스튜디오',
        'model': '모델:',
        'token_placeholder': 'HF 토큰 (선택)',
        'download': '다운로드 / 확인',
        'download_start': '{model} 다운로드 시작...',
        'download_finished': '다운로드 완료: {msg}',
        'download_failed': '다운로드 실패: {msg}',
        'prompt_placeholder': '프롬프트 입력 (예: 황혼의 미래 도시, 매우 상세함)',
        'generate': '이미지 생성',
        'prompt_empty': '프롬프트가 비어있습니다 - 내용을 입력하세요.',
        'select_output': '출력 폴더 선택',
        'output_selected': '출력 폴더: {path}',
        'cancel_download': '다운로드 취소',
        'device': '장치:',
        'precision': '정밀도:',
        'scheduler': 'Scheduler:',
        'language': '언어:',
        'preview': '최근 미리보기',
        'open_output': '출력 폴더 열기',
        'clear_cache': '모델 캐시 지우기',
        'cache_cleared': '모델 캐시 지워짐.',
        'no_images': '생성된 이미지가 없습니다.',
        'preview_failed': '미리보기 실패: {msg}',
        'settings': '설정',
        'settings_title': '설정',
        'settings_saved': '설정 저장 및 적용 완료.',
    },

    'ar': {
        'title': 'AIVISTA Pro - استوديو الصور',
        'model': 'النموذج:',
        'token_placeholder': 'رمز HF (اختياري)',
        'download': 'تنزيل / التأكد',
        'download_start': 'بدء تنزيل النموذج {model}...',
        'download_finished': 'تم الانتهاء من التنزيل: {msg}',
        'download_failed': 'فشل التنزيل: {msg}',
        'prompt_placeholder': 'أدخل الوصف (مثال: مدينة مستقبلية عند الغسق، مفصلة للغاية)',
        'generate': 'توليد الصورة',
        'prompt_empty': 'الوصف فارغ - أدخل شيئًا.',
        'select_output': 'اختر مجلد الإخراج',
        'output_selected': 'تم تعيين مجلد الإخراج: {path}',
        'cancel_download': 'إلغاء التنزيل',
        'device': 'الجهاز:',
        'precision': 'الدقة:',
        'scheduler': 'Scheduler:',
        'language': 'اللغة:',
        'preview': 'معاينة الأحدث',
        'open_output': 'فتح مجلد الإخراج',
        'clear_cache': 'مسح ذاكرة نموذج التخزين المؤقت',
        'cache_cleared': 'تم مسح ذاكرة نموذج التخزين المؤقت.',
        'no_images': 'لم يتم العثور على صور مولدة.',
        'preview_failed': 'فشل المعاينة: {msg}',
        'settings': 'الإعدادات',
        'settings_title': 'الإعدادات',
        'settings_saved': 'تم حفظ الإعدادات وتطبيقها.',
    },

    'hi': {
        'title': 'AIVISTA Pro - इमेज स्टूडियो',
        'model': 'मॉडल:',
        'token_placeholder': 'HF टोकन (वैकल्पिक)',
        'download': 'डाउनलोड / सुनिश्चित करें',
        'download_start': '{model} के लिए डाउनलोड शुरू हो रहा है...',
        'download_finished': 'डाउनलोड समाप्त: {msg}',
        'download_failed': 'डाउनलोड विफल: {msg}',
        'prompt_placeholder': 'प्रॉम्प्ट दर्ज करें (उदा. सांझ में भविष्यवादी शहर, बहुत विस्तृत)',
        'generate': 'इमेज जनरेट करें',
        'prompt_empty': 'प्रॉम्प्ट खाली - कुछ दर्ज करें।',
        'select_output': 'आउटपुट फोल्डर चुनें',
        'output_selected': 'आउटपुट फोल्डर सेट किया गया: {path}',
        'cancel_download': 'डाउनलोड रद्द करें',
        'device': 'डिवाइस:',
        'precision': 'सटीकता:',
        'scheduler': 'Scheduler:',
        'language': 'भाषा:',
        'preview': 'नवीनतम पूर्वावलोकन',
        'open_output': 'आउटपुट फोल्डर खोलें',
        'clear_cache': 'मॉडल कैश साफ़ करें',
        'cache_cleared': 'मॉडल कैश साफ़ किया गया।',
        'no_images': 'कोई जेनरेट की गई छवि नहीं मिली।',
        'preview_failed': 'पूर्वावलोकन विफल: {msg}',
        'settings': 'सेटिंग्स',
        'settings_title': 'सेटिंग्स',
        'settings_saved': 'सेटिंग्स सहेजी गईं और लागू की गईं।',
    },

    'id': {
        'title': 'AIVISTA Pro - Studio Gambar',
        'model': 'Model:',
        'token_placeholder': 'Token HF (opsional)',
        'download': 'Unduh / Pastikan',
        'download_start': 'Memulai unduhan model {model}...',
        'download_finished': 'Unduhan selesai: {msg}',
        'download_failed': 'Unduhan gagal: {msg}',
        'prompt_placeholder': 'Masukkan prompt (mis. kota futuristik saat senja, sangat detail)',
        'generate': 'Hasilkan Gambar',
        'prompt_empty': 'Prompt kosong - masukkan sesuatu.',
        'select_output': 'Pilih Folder Output',
        'output_selected': 'Folder output: {path}',
        'cancel_download': 'Batalkan unduhan',
        'device': 'Perangkat:',
        'precision': 'Presisi:',
        'scheduler': 'Scheduler:',
        'language': 'Bahasa:',
        'preview': 'Pratinjau Terbaru',
        'open_output': 'Buka folder output',
        'clear_cache': 'Bersihkan cache model',
        'cache_cleared': 'Cache model dibersihkan.',
        'no_images': 'Tidak ada gambar yang ditemukan.',
        'preview_failed': 'Pratinjau gagal: {msg}',
        'settings': 'Pengaturan',
        'settings_title': 'Pengaturan',
        'settings_saved': 'Pengaturan disimpan dan diterapkan.',
    },

    'ro': {
        'title': 'AIVISTA Pro - Studioul de Imagine',
        'model': 'Model:',
        'token_placeholder': 'Token HF (opţional)',
        'download': 'Descarcă / Asigură',
        'download_start': 'Începe descărcarea modelului {model}...',
        'download_finished': 'Descărcare finalizată: {msg}',
        'download_failed': 'Descărcare eșuată: {msg}',
        'prompt_placeholder': 'Introduceți prompt-ul (ex. oraș futurist la amurg, foarte detaliat)',
        'generate': 'Generează Imagine',
        'prompt_empty': 'Prompt gol - introduceți ceva.',
        'select_output': 'Alege folderul de ieșire',
        'output_selected': 'Folder de ieșire: {path}',
        'cancel_download': 'Anulează descărcarea',
        'device': 'Dispozitiv:',
        'precision': 'Precizie:',
        'scheduler': 'Scheduler:',
        'language': 'Limbă:',
        'preview': 'Previzualizează ultima',
        'open_output': 'Deschide folderul de ieșire',
        'clear_cache': 'Curăță cache-ul modelelor',
        'cache_cleared': 'Cache-ul modelelor curățat.',
        'no_images': 'Nu s-au găsit imagini generate.',
        'preview_failed': 'Previzualizare eșuată: {msg}',
        'settings': 'Setări',
        'settings_title': 'Setări',
        'settings_saved': 'Setările au fost salvate și aplicate.',
    },

    'vi': {
        'title': 'AIVISTA Pro - Studio Hình Ảnh',
        'model': 'Mô hình:',
        'token_placeholder': 'Token HF (tùy chọn)',
        'download': 'Tải xuống / Đảm bảo',
        'download_start': 'Bắt đầu tải xuống mô hình {model}...',
        'download_finished': 'Tải xuống hoàn tất: {msg}',
        'download_failed': 'Tải xuống thất bại: {msg}',
        'prompt_placeholder': 'Nhập prompt (ví dụ: thành phố tương lai lúc hoàng hôn, rất chi tiết)',
        'generate': 'Tạo ảnh',
        'prompt_empty': 'Prompt trống - hãy nhập nội dung.',
        'select_output': 'Chọn thư mục đầu ra',
        'output_selected': 'Thư mục đầu ra: {path}',
        'cancel_download': 'Hủy tải xuống',
        'device': 'Thiết bị:',
        'precision': 'Độ chính xác:',
        'scheduler': 'Scheduler:',
        'language': 'Ngôn ngữ:',
        'preview': 'Xem trước mới nhất',
        'open_output': 'Mở thư mục đầu ra',
        'clear_cache': 'Xóa cache mô hình',
        'cache_cleared': 'Cache mô hình đã được xóa.',
        'no_images': 'Không tìm thấy ảnh đã tạo.',
        'preview_failed': 'Xem trước thất bại: {msg}',
        'settings': 'Cài đặt',
        'settings_title': 'Cài đặt',
        'settings_saved': 'Cài đặt đã lưu và áp dụng.',
    },
}

DEFAULT_LANG = 'en'

# Settings dialog

class SettingsDialog(QDialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.parent = parent
        self.settings = settings

        title = parent.trans.get('settings_title', parent.trans.get('settings', 'Settings'))
        self.setWindowTitle(title)

        layout = QFormLayout(self)

        # CPU percent
        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 100)
        self.cpu_spin.setValue(int(getattr(self.settings, 'cpu_limit_percent', 70)))
        self.cpu_spin.setToolTip(parent.trans.get('device', 'CPU limit percent'))

        # RAM percent
        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(1, 100)
        self.ram_spin.setValue(int(getattr(self.settings, 'ram_limit_percent', 95)))
        self.ram_spin.setToolTip(parent.trans.get('device', 'RAM limit percent'))

        # GPU percent
        self.gpu_spin = QSpinBox()
        self.gpu_spin.setRange(1, 100)
        self.gpu_spin.setValue(int(getattr(self.settings, 'gpu_limit_percent', 95)))
        self.gpu_spin.setToolTip(parent.trans.get('device', 'GPU limit percent'))

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 30.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setValue(float(getattr(self.settings, 'monitor_interval', 1.0)))
        self.interval_spin.setToolTip("Monitor interval (seconds)")

        layout.addRow("CPU %", self.cpu_spin)
        layout.addRow("RAM %", self.ram_spin)
        layout.addRow("GPU %", self.gpu_spin)
        layout.addRow("Monitor (s)", self.interval_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def accept(self):
        try:
            cpu = int(self.cpu_spin.value())
            ram = int(self.ram_spin.value())
            gpu = int(self.gpu_spin.value())
            interval = float(self.interval_spin.value())

            self.settings.cpu_limit_percent = cpu
            self.settings.ram_limit_percent = ram
            self.settings.gpu_limit_percent = gpu
            self.settings.monitor_interval = interval

            # Respect clamp rules from Settings
            try:
                self.settings.clamp()
            except Exception:
                pass

            super().accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Invalid settings: {e}")
            return

# MainWindow

class MainWindow(QMainWindow):
    LANGUAGE_NAMES = {
    'en': 'English',
    'sr_lat': 'Srpski (latinica)',
    'sr_cyr': 'Српски (ћирилица)',
    'es': 'Español',
    'ru': 'Русский',
    'fr': 'Français',
    'de': 'Deutsch',
    'it': 'Italiano',
    'pt': 'Português',
    'pt_br': 'Português (BR)',
    'nl': 'Nederlands',
    'sv': 'Svenska',
    'fi': 'Suomi',
    'no': 'Norsk',
    'da': 'Dansk',
    'ja': '日本語',
    'ko': '한국어',
    'zh_cn': '中文 (简体)',
    'zh_tw': '中文 (繁體)',
    'ar': 'العربية',
    'tr': 'Türkçe',
    'pl': 'Polski',
    'hi': 'हिन्दी',
    'id': 'Bahasa Indonesia',
    'ro': 'Română',
    'vi': 'Tiếng Việt'
}

    def __init__(self):
        super().__init__()
        # default language
        self.lang = DEFAULT_LANG
        self.trans = TRANSLATIONS[self.lang]
        self.init_language_combo()

        self.setWindowTitle(self.trans['title'])
        self.setMinimumSize(1000, 700)
        self.downloader: Optional[QThread] = None
        self.generator: Optional[QThread] = None
        self._output_folder = str(IMAGE_DIR)

        # resource controller
        self.resource_controller = ResourceController()
        self.resource_controller.apply_soft_limits()
        self.resource_controller.start_monitor()

        # Build UI first so logging is available
        self._build_ui()

        # Apply embedded QSS (no external file needed)
        self._apply_qss()

        # Small header animation to give "pro" feel
        self._animate_header()

        self.log_msg("[INFO] UI ready.")
        self.log_msg(f"[INFO] Resource monitor started (cpu={self.resource_controller.settings.cpu_limit_percent}%, ram={self.resource_controller.settings.ram_limit_percent}%, gpu={self.resource_controller.settings.gpu_limit_percent}%).")

    # language init helpers

    def init_language_combo(self):
        keys = list(self.LANGUAGE_NAMES.keys())
        for k in TRANSLATIONS.keys():
            if k not in keys:
                keys.append(k)
        seen = set()
        ordered = []
        for k in keys:
            if k not in seen:
                ordered.append(k)
                seen.add(k)
        self.lang_codes = ordered

    def populate_language_combo(self):
        self.lang_combo.clear()
        for code in self.lang_codes:
            display = self.LANGUAGE_NAMES.get(code, code)
            self.lang_combo.addItem(display)
        if self.lang in self.lang_codes:
            try:
                self.lang_combo.setCurrentIndex(self.lang_codes.index(self.lang))
            except Exception:
                pass
                
    def _apply_qss(self):
       qss = """
       /* Root colors */
       QWidget, QMainWindow {
           background-color: #0b0b0d;
           color: #e6e6e6;
           font-family: "Segoe UI", Arial, sans-serif;
           font-size: 13px;
       }

       /* Header */
       QLabel#header {
           font-size: 18px;
           font-weight: 700;
           color: #e6e6e6;
           padding: 16px 24px;
       }

       /* Panels / cards */
       QFrame, QGroupBox {
           background-color: #121214;
           border: 1px solid #232326;
           border-radius: 10px;
           padding: 16px;
       }

       /* Labels */
       QLabel {
           color: #8a8a8a;
           font-weight: 600;
       }

       /* Inputs */
       QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
           background-color: #161618;
           color: #e6e6e6;
           border: 1px solid #232326;
           border-radius: 6px;
           padding: 8px;
       }
       QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
           border: 1px solid #bf7a2a;
           background-color: #18181a;
       }

       QTextEdit#log, QTextEdit[readOnly="true"] {
           background-color: #0f0f11;
           border: 1px solid #232326;
           border-radius: 6px;
           padding: 10px;
           color: #d0d0d0;
           min-height: 160px;
       }

       /* Buttons */
       QPushButton {
           background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1f1f20, stop:1 #141415);
           color: #e6e6e6;
           border: 1px solid #2b2b2c;
           border-radius: 8px;
           padding: 10px 12px;
           font-weight: 700;
           cursor: pointer;
       }
       QPushButton:hover {
           border: 1px solid #bf7a2a;
           transform: scale(1.01);
       }

       /* Progress bars */
       QProgressBar {
           background-color: #161618;
           border: 1px solid #232326;
           border-radius: 8px;
           height: 14px;
           text-align: center;
       }
       QProgressBar::chunk {
           background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #bf7a2a, stop:1 #f0c38a);
           border-radius: 8px;
       }

       /* Combo boxes */
       QComboBox {
           padding: 8px;
           border-radius: 6px;
           min-height: 34px;
           background-color: #161618;
           color: #e6e6e6;
           border: 1px solid #232326;
       }
       QComboBox::drop-down {
           subcontrol-origin: padding;
           subcontrol-position: top right;
           width: 26px;
           border-left: 1px solid #232326;
       }
       QComboBox QAbstractItemView {
           background-color: #161618;
           border: 1px solid #232326;
           selection-background-color: #444444;
           selection-color: #ffffff;
           padding: 4px;
       }

       /* Scrollbars */
       QScrollBar:vertical {
           background: transparent;
           width: 12px;
           margin: 12px 0;
       }
       QScrollBar::handle:vertical {
           background: #2a2a2d;
           min-height: 20px;
           border-radius: 6px;
       }
       QScrollBar::handle:vertical:hover {
           background: #3b3b3d;
       }

       /* Preview */
       QLabel#preview {
           background-color: #0e0e0f;
           border: 1px dashed #2b2b2d;
           border-radius: 6px;
           padding: 8px;
           color: #8a8a8a;
       }

       /* Footer / muted text */
       QLabel[role="muted"], #status, QStatusBar {
           color: #8a8a8a;
           font-size: 13px;
       }

       /* Errors */
       QLabel.error {
           color: #ff6b6b;
           font-weight: 700;
       }

       /* Tooltips */
       QToolTip {
           background-color: #121214;
           color: #e6e6e6;
           border: 1px solid #232326;
           padding: 6px;
           border-radius: 6px;
       }
       """
       app = QApplication.instance()
       if app:
           app.setStyleSheet(qss)
       else:
           self.setStyleSheet(qss)
       try:
           self.log_msg("[INFO] Applied dark QSS.")
       except Exception:
           pass

    # small header animation
    def _animate_header(self):
        header = self.findChild(QLabel, 'header')
        if not header:
            return
        effect = QGraphicsOpacityEffect(header)
        header.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(800)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # UI build

    def _build_ui(self):
        font = QFont('Segoe UI', 10)
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout()
        central.setLayout(v)

        header = QLabel(self.trans['title'])
        header.setObjectName('header')  # QSS target
        header.setFont(QFont('Segoe UI', 16))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(header)

        # Top controls: model + token + language
        top_h = QHBoxLayout()
        v.addLayout(top_h)

        top_h.addWidget(QLabel(self.trans['model']))
        self.model_combo = QComboBox()
        self.model_combo.addItems([
            # SDXL (base + refiner)
            "stabilityai/stable-diffusion-xl-base-1.0",
            "stabilityai/stable-diffusion-xl-refiner-1.0",

            # Stability AI / official family
            "stabilityai/stable-diffusion-2-1",
            "stabilityai/stable-diffusion-2-1-base",
            "stabilityai/stable-diffusion-2-1-unclip-small",
            "stabilityai/stable-diffusion-2",
            "stabilityai/stable-diffusion-x4-upscaler",
            "stabilityai/sdxl-vae",
            "stabilityai/sd-vae-ft-mse-original",
            "stabilityai/stable-diffusion-3-medium",
            "stabilityai/stable-diffusion-3.5-large",

            # Classic / community Stable Diffusion checkpoints
            "CompVis/stable-diffusion-v1-4",
            "CompVis/stable-diffusion-v-1-4-original",
            "stable-diffusion-v1-5/stable-diffusion-v1-5",   # mirror / commonly used name

            # Dreamlike / photoreal and similar
            "dreamlike-art/dreamlike-photoreal-2.0",
            "gsdf/Counterfeit-V2.5",
            "gsdf/Counterfeit-V3.0",

            # OpenJourney / Midjourney-style
            "prompthero/openjourney-v4",
            "prompthero/openjourney",

            # Anime / "Anything" family
            "andite/anything-v4.0",
            "xyn-ai/anything-v4.0",
            "hakurei/waifu-diffusion-v1-4",
            "hakurei/waifu-diffusion",

            # Realistic Vision / high-quality photoreal models
            "SG161222/Realistic_Vision_V2.0",
            "SG161222/Realistic_Vision_V5.0_noVAE",
            "SG161222/RealVisXL_V1.0",
            "SG161222/RealVisXL_V2.0",
            "SG161222/Realistic_Vision_V6.0_B1_noVAE",

            # Kandinsky / other research models
            "kandinsky-community/kandinsky-2-2",
            "kandinsky-community/kandinsky-2-2-decoder",

            # Inpainting / edit / instruct-style models
            "timbrooks/instruct-pix2pix",

            # Community & special-purpose
            "nitrosocke/classic-anim-diffusion",
            "lambdalabs/sd-image-variations-diffusers",
            "madebyollin/taesd-x4-upscaler",
            "stablediffusionapi/realistic-vision-v20-2047",
            "stablediffusionapi/realistic-vision-2",

            # Mirrors / helpful community copies
            "rupeshs/LCM-runwayml-stable-diffusion-v1-5",
            "prompthero-diffusion-models/openjourney-v4",

            # Utility / adapters / creative mixtures
            "h94/IP-Adapter",
            "WarriorMama777/OrangeMixs",
            "Kwai-Kolors/Kolors",
            "neta-art/Neta-Lumina",
            "duongve/NetaYume-Lumina-Image-2.0",

            # Extra community favourites (diversity)
            "SG161222/Realistic_Vision_V3.0_VAE",
            "SG161222/Realistic_Vision_V4.0_noVAE",
            "SG161222/RealVisXL_V3.0",
            "shibal1/anything-v4.5-clone",
            "x90/enterprise-demo-model"
        ])

        top_h.addWidget(self.model_combo)

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText(self.trans['token_placeholder'])
        top_h.addWidget(self.token_input)

        # Language selector
        top_h.addWidget(QLabel(self.trans['language']))
        self.lang_combo = QComboBox()
        # populate languages from our prepared list
        self.populate_language_combo()
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        top_h.addWidget(self.lang_combo)

        # Settings button (NEW)
        self.btn_settings = QPushButton(self.trans.get('settings', "Settings"))
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        top_h.addWidget(self.btn_settings)

        # Download / Cancel
        self.btn_download = QPushButton(self.trans['download'])
        self.btn_download.clicked.connect(self.handle_download)
        top_h.addWidget(self.btn_download)

        self.btn_cancel_download = QPushButton(self.trans['cancel_download'])
        self.btn_cancel_download.clicked.connect(self._cancel_download)
        self.btn_cancel_download.setEnabled(False)
        top_h.addWidget(self.btn_cancel_download)

        # Extra action buttons (Preview, Open, Clear)
        self.btn_preview = QPushButton(self.trans['preview'])
        self.btn_preview.clicked.connect(self._preview_latest_image)
        top_h.addWidget(self.btn_preview)

        self.btn_open_output = QPushButton(self.trans['open_output'])
        self.btn_open_output.clicked.connect(self._open_output_folder)
        top_h.addWidget(self.btn_open_output)

        self.btn_clear_cache = QPushButton(self.trans['clear_cache'])
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        top_h.addWidget(self.btn_clear_cache)

        # progress bar
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        v.addWidget(self.download_progress)

        # Options row: device, precision, scheduler, output folder
        opts = QHBoxLayout()
        v.addLayout(opts)

        opts.addWidget(QLabel(self.trans['device']))
        self.device_combo = QComboBox()
        self.device_combo.addItems(['auto', 'cuda', 'cpu'])
        opts.addWidget(self.device_combo)

        opts.addWidget(QLabel(self.trans['precision']))
        self.precision_combo = QComboBox()
        self.precision_combo.addItems(['auto', 'float16', 'float32'])
        opts.addWidget(self.precision_combo)

        opts.addWidget(QLabel(self.trans['scheduler']))
        self.scheduler_combo = QComboBox()
        self.scheduler_combo.addItems(['DDIM', 'DDPM', 'PNDM', 'LMS', 'Euler'])
        opts.addWidget(self.scheduler_combo)

        # Output folder selector
        self.output_btn = QPushButton(self.trans['select_output'])
        self.output_btn.clicked.connect(self._choose_output_folder)
        opts.addWidget(self.output_btn)
        self.output_label = QLabel(self._output_folder)
        self.output_label.setMinimumWidth(240)
        opts.addWidget(self.output_label)

        # Generator options
        opts2 = QHBoxLayout()
        v.addLayout(opts2)

        opts2.addWidget(QLabel('Width'))
        self.width_spin = QSpinBox(); self.width_spin.setRange(256, 2048); self.width_spin.setValue(1024)
        opts2.addWidget(self.width_spin)

        opts2.addWidget(QLabel('Height'))
        self.height_spin = QSpinBox(); self.height_spin.setRange(256, 2048); self.height_spin.setValue(1024)
        opts2.addWidget(self.height_spin)

        opts2.addWidget(QLabel('Steps'))
        self.steps_spin = QSpinBox(); self.steps_spin.setRange(1, 200); self.steps_spin.setValue(30)
        opts2.addWidget(self.steps_spin)

        opts2.addWidget(QLabel('CFG'))
        self.cfg_spin = QDoubleSpinBox(); self.cfg_spin.setRange(1.0, 30.0); self.cfg_spin.setSingleStep(0.5); self.cfg_spin.setValue(7.5)
        opts2.addWidget(self.cfg_spin)

        opts2.addWidget(QLabel('Filename'))
        self.filename_input = QLineEdit('city.png')
        opts2.addWidget(self.filename_input)

        # Prompt area
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText(self.trans['prompt_placeholder'])
        v.addWidget(self.prompt_edit)

        # Buttons row: generate + progress
        hbtn = QHBoxLayout()
        v.addLayout(hbtn)

        self.btn_gen = QPushButton(self.trans['generate'])
        self.btn_gen.clicked.connect(self.handle_generate)
        hbtn.addWidget(self.btn_gen)

        self.gen_progress = QProgressBar(); self.gen_progress.setRange(0, 100)
        hbtn.addWidget(self.gen_progress)

        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setFixedHeight(160)
        self.log.setObjectName('log')
        v.addWidget(self.log)

    # i18n helpers

    def _on_language_changed(self, index: int):
        try:
            if index < 0 or index >= len(self.lang_codes):
                return
            code = self.lang_codes[index]
            self.set_language(code)
        except Exception:
            pass

    def set_language(self, lang_code: str):
        if lang_code not in TRANSLATIONS:
            self.log_msg(f"[WARN] Language code '{lang_code}' not found!")
            return
        self.lang = lang_code
        self.trans = TRANSLATIONS[self.lang]

        # Update GUI
        self.setWindowTitle(self.trans['title'])
        header = self.findChild(QLabel, 'header')
        if header:
            header.setText(self.trans['title'])
        self.token_input.setPlaceholderText(self.trans['token_placeholder'])
        self.btn_download.setText(self.trans['download'])
        self.btn_cancel_download.setText(self.trans['cancel_download'])
        self.btn_gen.setText(self.trans['generate'])
        self.prompt_edit.setPlaceholderText(self.trans['prompt_placeholder'])
        self.output_btn.setText(self.trans['select_output'])
        self.btn_preview.setText(self.trans['preview'])
        self.btn_open_output.setText(self.trans['open_output'])
        self.btn_clear_cache.setText(self.trans['clear_cache'])

        # Tooltips
        self.device_combo.setToolTip(self.trans['device'])
        self.precision_combo.setToolTip(self.trans['precision'])
        self.scheduler_combo.setToolTip(self.trans['scheduler'])
        self.lang_combo.setToolTip(self.trans['language'])

        # Settings dugme i naslovi
        if hasattr(self, 'btn_settings'):
            self.btn_settings.setText(self.trans.get('settings', 'Settings'))
        self.log_msg(f"[INFO] Language set to: {self.lang}")

    # Settings dialog

    def open_settings_dialog(self):
        dlg = SettingsDialog(self, self.resource_controller.settings)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # settings object was modified in dialog; persist and apply
            self.resource_controller.settings.clamp()
            self.resource_controller.save()
            self.resource_controller.apply_soft_limits()
            # restart monitor to pick up new interval quickly
            self.resource_controller.stop_monitor()
            self.resource_controller.start_monitor()
            self.log_msg("[INFO] Settings updated and applied.")
            QMessageBox.information(self, self.trans.get('settings', "Settings"), self.trans.get('settings_saved', "Settings saved and applied."))

    # UI actions

    def _choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.trans['select_output'], str(Path.home()))
        if folder:
            self._output_folder = folder
            self.output_label.setText(folder)
            self.log_msg(self.trans['output_selected'].format(path=folder))

    def log_msg(self, txt: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{now}] {txt}")

    def handle_download(self):
        model_id = self.model_combo.currentText()
        token = self.token_input.text().strip() or None
        self.btn_download.setEnabled(False)
        self.btn_cancel_download.setEnabled(True)
        self.download_progress.setValue(0)
        self.log_msg(self.trans['download_start'].format(model=model_id))
        self.downloader = ModelDownloaderThread(model_id, str(CACHE_DIR), token)
        self.downloader.progress_changed.connect(self.on_download_progress)
        self.downloader.finished.connect(self.on_download_finished)
        self.downloader.start()

    def _cancel_download(self):
        if self.downloader and isinstance(self.downloader, QThread):
            try:
                if hasattr(self.downloader, "requestInterruption"):
                    self.downloader.requestInterruption()
                if hasattr(self.downloader, "terminate"):
                    self.downloader.terminate()
                self.log_msg("[INFO] Cancel requested for download thread.")
            except Exception as e:
                self.log_msg(f"[WARN] Could not cancel cleanly: {e}")
        self.btn_cancel_download.setEnabled(False)

    def on_download_progress(self, percent: int, msg: str):
        self.download_progress.setValue(percent)
        if msg:
            self.log_msg(msg)

    def on_download_finished(self, ok: bool, msg: str):
        self.btn_download.setEnabled(True)
        self.btn_cancel_download.setEnabled(False)
        self.download_progress.setValue(100 if ok else 0)
        if ok:
            self.log_msg(self.trans['download_finished'].format(msg=msg))
        else:
            self.log_msg(self.trans['download_failed'].format(msg=msg))

    def handle_generate(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            self.log_msg(self.trans['prompt_empty'])
            return
        model_key = self.model_combo.currentText()
        filename = self.filename_input.text().strip() or 'output.png'
        width = int(self.width_spin.value())
        height = int(self.height_spin.value())
        steps = int(self.steps_spin.value())
        guidance = float(self.cfg_spin.value())
        device = self.device_combo.currentText()
        precision = self.precision_combo.currentText()
        scheduler = self.scheduler_combo.currentText()

        # Re-apply soft limits right before heavy work
        self.resource_controller.apply_soft_limits()

        self.btn_gen.setEnabled(False)
        self.gen_progress.setValue(0)
        self.generator = ImageGeneratorThread(
            prompt, model_key, filename, width, height, steps, guidance,
            str(CACHE_DIR), self._output_folder,
            device=device, precision=precision, scheduler=scheduler
        )

        self.generator.progress_changed.connect(self.on_gen_progress)
        self.generator.finished.connect(self.on_gen_finished)
        if hasattr(self.generator, "log"):
            self.generator.log.connect(self.log_msg)
        self.generator.start()
        self.log_msg(f"[INFO] Generation started ({model_key}) on {device} / {precision} / {scheduler}")

        # Optionally try to attach hard limits via Rust helper (if binary exists)
        try:
            pid = getattr(self.generator, "pid", None)
            if pid:
                ok = self.resource_controller.enforce_hard_limits(pid)
                self.log_msg(f"[INFO] Tried enforce_hard_limits on pid {pid}: {ok}")
        except Exception:
            pass

    def on_gen_progress(self, percent: int):
        self.gen_progress.setValue(percent)

    def on_gen_finished(self, ok: bool, path_or_msg: str):
        self.btn_gen.setEnabled(True)
        if ok:
            self.log_msg(f"[DONE] Image generated: {path_or_msg}")
        else:
            self.log_msg(f"[ERROR] Generation failed: {path_or_msg}")

    # Extra helpers

    def _preview_latest_image(self):
        try:
            imgs = list(Path(self._output_folder).glob('*'))
            imgs = [p for p in imgs if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')]
            if not imgs:
                self.log_msg(self.trans['no_images'])
                return
            latest = max(imgs, key=lambda p: p.stat().st_mtime)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(latest)))
            self.log_msg(f"[INFO] Previewing: {latest.name}")
        except Exception as e:
            self.log_msg(self.trans['preview_failed'].format(msg=str(e)))

    def _open_output_folder(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_folder)))
            self.log_msg(f"[INFO] Opened output folder: {self._output_folder}")
        except Exception as e:
            self.log_msg(f"[WARN] Could not open folder: {e}")

    def _clear_cache(self):
        try:
            if CACHE_DIR.exists():
                shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir(exist_ok=True)
            self.log_msg(self.trans['cache_cleared'])
        except Exception as e:
            self.log_msg(f"[ERROR] Could not clear cache: {e}")

    def run_rust_optimizer(self):
        rust_binary = str(PROJECT_ROOT / "rust" / "optimizer" / "target" / "release" / "optimizer.exe")  # Windows
        self.rust_thread = RustOptimizerThread(rust_binary)
        self.rust_thread.log.connect(self.log_msg)
        self.rust_thread.finished.connect(self.on_rust_finished)
        self.rust_thread.start()
        self.log_msg("[INFO] Rust optimizer thread started.")

    def on_rust_finished(self, ok: bool, msg: str):
        if ok:
            self.log_msg(f"[RUST DONE] {msg}")
        else:
            self.log_msg(f"[RUST ERROR] {msg}")

    def closeEvent(self, event):
        # Save settings and stop monitor cleanly
        try:
            self.resource_controller.save()
            self.resource_controller.stop_monitor()
        except Exception:
            pass
        super().closeEvent(event)