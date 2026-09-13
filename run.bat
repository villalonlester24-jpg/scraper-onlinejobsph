@echo off
cd /d "%~dp0"

set OFFSET=%1
if "%OFFSET%"=="" set OFFSET=0

echo === Scraping OnlineJobs.PH (offset=%OFFSET%) ===
if exist jobs.json del jobs.json
python -m scrapy crawl jobs -a offset=%OFFSET% -o jobs.json
if errorlevel 1 goto :error

echo.
echo === Uploading to Google Sheets ===
python to_gsheet.py
if errorlevel 1 goto :error

echo.
echo Done.
goto :eof

:error
echo.
echo Something went wrong. Check the messages above.
pause
