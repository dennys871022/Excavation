@echo off
cd /d %~dp0
py -3.12 auto_manifest.py %*
