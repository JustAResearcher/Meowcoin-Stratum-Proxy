@echo off
title MITM Test - SRBMiner via MITM to 2miners
set SRBMINER=C:\Users\benef\Downloads\SRBMiner-Multi-3-1-2-win64\SRBMiner-Multi-3-1-2\SRBMiner-MULTI.exe
set POOL=stratum+tcp://127.0.0.1:3334
%SRBMINER% --algorithm kawpow --pool %POOL% --wallet RKsrbWoAECnRLVdXHBijGfsKM3ssNoMwmW --password x
pause
