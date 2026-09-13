@echo off
cd /d "%~dp0"

set OFFSET=%1
if "%OFFSET%"=="" set OFFSET=0

echo === Loading cached descriptions ===
python to_gsheet.py --export-cache cache.json
if errorlevel 1 goto :error

echo.
echo === Scraping OnlineJobs.PH (offset=%OFFSET%) ===
if exist jobs.json del jobs.json
python -m scrapy crawl jobs -a offset=%OFFSET% -a cache=cache.json -o jobs.json
if errorlevel 1 goto :error

echo.
echo === Replacing Google Sheet with this run's data ===
python to_gsheet.py --replace --jobs jobs.json
if errorlevel 1 goto :error

echo.
echo Done.
goto :eof

:error
echo.
echo Something went wrong. Check the messages above.
pause
