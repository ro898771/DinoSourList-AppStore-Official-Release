@echo off
rem Builds BoxFast (persistent Box helper used by the Python app) and copies it into ..\BoxLink-API.
rem Needs the .NET SDK once, on the build machine only. Users just need the .NET 6+ runtime BoxAutomate.exe already requires.
setlocal
cd /d "%~dp0"
where dotnet >nul 2>nul || (echo The .NET SDK was not found. & exit /b 1)
if exist "obj\out" rmdir /s /q "obj\out"
dotnet publish BoxFast.csproj -c Release -o obj\out --nologo -v q || exit /b 1
copy /y "obj\out\BoxFast.dll" "..\BoxLink-API\" >nul || (echo Could not copy to BoxLink-API - is BoxFast.exe still running? & exit /b 1)
copy /y "obj\out\BoxFast.exe" "..\BoxLink-API\" >nul || (echo Could not copy to BoxLink-API - is BoxFast.exe still running? & exit /b 1)
copy /y "obj\out\BoxFast.deps.json" "..\BoxLink-API\" >nul || (echo Could not copy to BoxLink-API - is BoxFast.exe still running? & exit /b 1)
copy /y "obj\out\BoxFast.runtimeconfig.json" "..\BoxLink-API\" >nul || (echo Could not copy to BoxLink-API - is BoxFast.exe still running? & exit /b 1)
copy /y "obj\out\BoxFast.pdb" "..\BoxLink-API\" >nul || (echo Could not copy to BoxLink-API - is BoxFast.exe still running? & exit /b 1)
echo BoxFast built into ..\BoxLink-API
