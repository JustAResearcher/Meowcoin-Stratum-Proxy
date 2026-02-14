@echo off
title Meowcoin Solo Miner - WildRig Multi

REM ============================================================
REM  Meowcoin Solo Mining via Stratum Proxy
REM  Wallet: MWcgFVkdBV32GP9HLd2ypQBoyTFNeb8zZ6
REM ============================================================

REM -- Edit this path to point to your wildrig.exe location --
set WILDRIG=wildrig.exe

REM -- Stratum proxy address (localhost default) --
set POOL=stratum+tcp://127.0.0.1:3333

%WILDRIG% --algo meowpow --url %POOL% --user MWcgFVkdBV32GP9HLd2ypQBoyTFNeb8zZ6 --pass x

pause
