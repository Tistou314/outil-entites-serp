@echo off
cd /d "%~dp0"
if "%APIFY_TOKEN%"=="" if not exist ".streamlit\secrets.toml" (
  set /p APIFY_TOKEN=Colle ton jeton API Apify puis Entree : 
)
python -m pip install -q -r requirements.txt
python -m streamlit run app.py
pause
