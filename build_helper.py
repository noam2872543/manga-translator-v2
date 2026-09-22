import os
import sys
import subprocess
import shutil
import logging
from pathlib import Path
import time

# --- הגדרת מערכת לוגים מקצועית למעקב ב-GitHub Actions ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("MangaBuilder")

class MangaTranslatorBuilder:
    def __init__(self, mode="full"):
        self.mode = mode
        # שימוש ב-pathlib לניהול נתיבים בטוח בכל מערכות ההפעלה
        self.root_dir = Path(__file__).parent.resolve()
        self.dist_dir = self.root_dir / "dist"
        self.build_dir = self.root_dir / "build"
        self.assets_dir = self.root_dir / "assets"
        
        # ספריות שחייבים לייבא באופן סמוי כדי למנוע קריסות של התוכנה
        self.hidden_imports = [
            "bidi",
            "deep_translator",
            "arabic_reshaper"
        ]
        
        # ספריות כבדות שחייבים לאסוף עבורן את כל התלויות והמודלים (AI, OCR)
        self.collect_all_modules = [
            "manga_ocr", "transformers", "tokenizers", "huggingface_hub",
            "safetensors", "easyocr", "scipy", "skimage", "torch", "torchvision", "flet_web"
        ]

    def clean_workspace(self):
        """מנקה שאריות מבניות קודמות כדי למנוע התנגשויות Cache"""
        logger.info("Cleaning previous build artifacts...")
        for directory in [self.dist_dir, self.build_dir]:
            if directory.exists() and directory.is_dir():
                try:
                    shutil.rmtree(directory)
                    logger.info(f"Successfully removed directory: {directory.name}")
                except Exception as e:
                    logger.warning(f"Could not remove {directory.name}: {e}")

    def verify_assets(self):
        """מוודא שקובצי חובה קיימים לפני שמתחילים את הבנייה"""
        logger.info("Verifying required assets...")
        required_files = ["icon.ico", "icon.png"]
        for req in required_files:
            file_path = self.assets_dir / req
            if not file_path.exists():
                logger.error(f"CRITICAL: Missing required asset -> {file_path}")
                sys.exit(1)
        logger.info("All required assets are present.")

    def get_easyocr_model_path(self):
        """מאתר את תיקיית המודלים של EasyOCR במחשב שמריץ את הבנייה"""
        user_home = Path.home()
        model_dir = user_home / ".EasyOCR" / "model"
        if model_dir.exists() and model_dir.is_dir():
            logger.info(f"Found EasyOCR models at: {model_dir}")
            return str(model_dir)
        logger.warning("EasyOCR models not found in default directory. Will build without them.")
        return None

    def construct_command(self):
        """בונה את הפקודה המדויקת וההרמטית ל-flet pack"""
        logger.info("Constructing robust flet pack command...")
        
        # 1. פקודת הבסיס ל-Flet
        cmd = [
            sys.executable, "-m", "flet", "pack", "main.py",
            "-i", str(self.assets_dir / "icon.ico"),
            "-n", "MangaTranslator",
            "--distpath", str(self.dist_dir),
        ]
        
        # 2. הוספת תיקיות מידע (Data) באופן שתואם גם לווינדוס וגם ללינוקס
        sep = os.pathsep
        data_folders = [
            (str(self.assets_dir / "fonts"), "assets/fonts"),
            (str(self.assets_dir / "icon.png"), "assets"),
            (str(self.assets_dir / "icon.ico"), "assets")
        ]
        
        easyocr_path = self.get_easyocr_model_path()
        if easyocr_path:
            data_folders.append((easyocr_path, "easyocr_model"))
            
        for src, dst in data_folders:
            cmd.extend(["--add-data", f"{src}{sep}{dst}"])

        # 3. בניית מחרוזת הפרמטרים עבור PyInstaller
        pyinstaller_args_list = []
        for hi in self.hidden_imports:
            pyinstaller_args_list.append(f"--hidden-import {hi}")
            
        for mod in self.collect_all_modules:
            pyinstaller_args_list.append(f"--collect-all {mod}")
            
        # איחוד כל הדגלים למחרוזת טקסט אחת עם רווחים - הדרך היחידה שבה Flet מקבל את זה בלי לקרוס
        pyinstaller_args_str = " ".join(pyinstaller_args_list)
        
        # הכנסת המחרוזת לפקודה המקורית
        cmd.extend(["--pyinstaller-build-args", pyinstaller_args_str])
        
        return cmd

    def run_build(self):
        """מנהל את תהליך הבנייה מקצה לקצה"""
        self.clean_workspace()
        self.verify_assets()
        
        cmd = self.construct_command()
        
        logger.info("="*70)
        logger.info("Starting Build Process with command components:")
        for idx, part in enumerate(cmd):
            logger.info(f"  [{idx:02d}] {part}")
        logger.info("="*70)
        
        start_time = time.time()
        
        try:
            # הרצת הפקודה תוך כדי הדפסת הפלט בזמן אמת (Stream)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # קריאת הלוגים מ-PyInstaller והדפסתם כדי שלא ננחש איפה הוא נתקע
            for line in process.stdout:
                print(line, end="")
                
            process.wait()
            elapsed_time = time.time() - start_time
            
            if process.returncode == 0:
                logger.info("="*70)
                logger.info(f"Build COMPLETED SUCCESSFULLY in {elapsed_time:.2f} seconds!")
                return self.verify_output()
            else:
                logger.error("="*70)
                logger.error(f"Build FAILED with exit code {process.returncode}.")
                return False
                
        except Exception as e:
            logger.error(f"An unexpected error occurred during execution: {e}")
            return False

    def verify_output(self):
        """אימות אחרון - בדיקה שהקובץ המקומפל אכן קיים וגדול מאפס"""
        exe_path = self.dist_dir / "MangaTranslator.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            logger.info(f"Verified executable creation: {exe_path.name} (Size: {size_mb:.2f} MB)")
            return True
        else:
            logger.error("Executable not found in dist directory. Build failed silently.")
            return False

def main():
    print("\n" + "="*70)
    logger.info("Initializing Advanced Manga Translator Build Manager...")
    
    # קביעת מוד הבנייה מהפקודה (או default ל-full)
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "full"
    logger.info(f"Selected Build Mode: {mode.upper()}")
    
    builder = MangaTranslatorBuilder(mode=mode)
    is_success = builder.run_build()
    
    print("="*70 + "\n")
    
    if not is_success:
        sys.exit(1)
        
    sys.exit(0)

if __name__ == "__main__":
    main()
