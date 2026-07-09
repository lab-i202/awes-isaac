@echo off
setlocal

set "PROJECT=kite-isaac"

mkdir "%PROJECT%"
mkdir "%PROJECT%\profiles"
mkdir "%PROJECT%\assets"
mkdir "%PROJECT%\gui"
mkdir "%PROJECT%\scenario"
mkdir "%PROJECT%\utils"

type nul > "%PROJECT%\main.py"
type nul > "%PROJECT%\assets\asset_registry.json"
type nul > "%PROJECT%\gui\__init__.py"
type nul > "%PROJECT%\scenario\__init__.py"
type nul > "%PROJECT%\scenario\tethered_glider_scene.py"
type nul > "%PROJECT%\utils\__init__.py"
type nul > "%PROJECT%\utils\profile_io.py"
type nul > "%PROJECT%\utils\asset_io.py"

echo Project skeleton created successfully:
echo %PROJECT%
pause