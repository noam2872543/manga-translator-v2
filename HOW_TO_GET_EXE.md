# איך לקבל את ה-`MangaTranslator.exe` בלי לבנות אצלך

> **למה אי אפשר להוריד `.exe` ישירות?**
> PyInstaller לא יודע cross-compile: בינארי שנבנה על לינוקס (הסביבה פה)
> הוא בינארי לינוקס בלבד ולא ירוץ על Windows. כדי לייצר `.exe` אמיתי
> חייבים לבנות על מערכת Windows. במקום שתתקין Python + תלויות במחשב
> שלך, אנחנו בונים בענן דרך **GitHub Actions** (חינם).

## שלבים (פעם אחת, ~5 דקות הקמה)

1. **צור חשבון GitHub חינם** (אם אין לך) — https://github.com/signup
2. **צור ריפוזיטורי חדש, ריק, ציבורי** — https://github.com/new
   - שם: `manga-translator`
   - Public (חינם, עם Actions ללא הגבלה)
   - **אל** תסמן "Initialize with README" (כדי שיהיה ריק)
3. **העלה את הקבצים**:
   - חלץ את `manga-translator.zip` (הקובץ שקיבלת) לתיקייה מקומית
   - ב-GitHub, בעמוד הריפוזיטורי הריק, לחץ **"uploading an existing file"**
   - גרור את **כל** תוכן תיקיית `manga-translator/` (כולל תת-התיקיות
     `core/`, `gui/`, `assets/`, `.github/`) אל חלון ההעלאה
   - לחץ **Commit changes**
4. **הפעל את ה-build**:
   - לך ללשונית **Actions**
   - בצד שמאל לחץ **"Build Windows .exe"**
   - לחץ **Run workflow** → בחר `full` (מומלץ: OCR עובד מהקופסה) או
     `lite` (קטן יותר, ~186MB) → לחץ **Run workflow**
5. **חכה** ~10-15 דקות (full) או ~3 דקות (lite)
6. **הורד את ה-`.exe`**:
   - לחץ על ה-run הירוק
   - גלול למטה ל-**Artifacts**
   - לחץ `MangaTranslator-windows-full` (או `lite`)
   - קובץ `MangaTranslator-windows.zip` יורד
   - חלץ → **לחץ כפול על `MangaTranslator.exe`** → האפליקציה נפתחת!

> 💡 מעתה ואילך, כל פעם שמישהו דוחף שינוי ל-main, ה-build רץ אוטומטית
> (lite) וה-`.exe` המעודכן זמין ב-Artifacts תוך דקות.

## אלטרנטיבה: לבנות במחשב Windows משלך

אם יש לך מחשב Windows ואתה מעדיף build מקומי:

```bat
cd manga-translator
build_windows.bat            REM full ~2GB, OCR עובד מהקופסה
build_windows.bat lite       REM ~186MB
build_windows.bat dir       REM תיקייה ניידת
```

דרישות: Python 3.10-3.12 מותקן (https://python.org). הסקריפט מתקין
אוטומטית את כל התלויות + PyInstaller.

## מה לא בא עם עדכון .exe?

קובץ ה-`.exe` (full) מכיל **הכל**: Python, Flet, OpenCV, Pillow,
manga-ocr + PyTorch (מודל ה-OCR), deep-translator, פונטים עבריים,
והאייקון. **אתה לא צריך להתקין כלום** — רק לחיצה כפולה והאפליקציה נפתחת.

מגבלה יחידה: גודל ~2GB (בגלל PyTorch). אם זה בעייתי — השתמש ב-build
`lite` (~186MB) ואז התקן manga-ocr נפרד:
```bat
pip install manga-ocr
```
