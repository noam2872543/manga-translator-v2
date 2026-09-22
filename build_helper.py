import os
import sys
import subprocess

def find_easyocr_models():
    user_home = os.path.expanduser("~")
    model_dir = os.path.join(user_home, ".EasyOCR", "model")
    if os.path.exists(model_dir):
        return model_dir
    return None

def build_argv(mode="full"):
    # קוראים ישירות ל-PyInstaller במקום ל-flet pack
    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed", # מסתיר את חלון ה-CMD השחור ברקע
        "main.py",
        "-i", "assets/icon.ico",
        "-n", "MangaTranslator",
        "--distpath", "dist",
        "--add-data", f"assets/fonts{os.pathsep}assets/fonts",
        "--add-data", f"assets/icon.png{os.pathsep}assets",
        "--add-data", f"assets/icon.ico{os.pathsep}assets",
        "--hidden-import", "bidi",
        "--hidden-import", "deep_translator",
        "--hidden-import", "arabic_reshaper",
    ]

    easyocr_dir = find_easyocr_models()
    if easyocr_dir:
        argv.extend(["--add-data", f"{easyocr_dir}{os.pathsep}easyocr_model"])

    # PyInstaller מקבל את רשימת ה-collect-all באופן טבעי וללא שגיאות
    modules_to_collect = [
        "manga_ocr", "transformers", "tokenizers", "huggingface_hub",
        "safetensors", "easyocr", "scipy", "skimage", "torch", "torchvision", "flet_web"
    ]
    
    for mod in modules_to_collect:
        argv.extend(["--collect-all", mod])

    return argv

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    cmd = build_argv(mode)
    print("Executing standard PyInstaller build command...")
    res = subprocess.run(cmd)
    sys.exit(res.returncode)
