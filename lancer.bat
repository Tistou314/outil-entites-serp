@echo off
cd /d "%~dp0"
if "%GOOGLE_NL_API_KEY%"=="" if not exist ".streamlit\secrets.toml" (
  set /p GOOGLE_NL_API_KEY=Colle ta cle API Google Natural Language puis Entree : 
)
python -m pip install -q -r requirements.txt
python -m streamlit run app.py
pause
