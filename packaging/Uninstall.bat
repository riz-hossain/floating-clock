@echo off
title Floating Clock  -  Uninstall
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
