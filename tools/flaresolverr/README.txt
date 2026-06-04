Put FlareSolverr.exe here if you want the panel to start the local binary:

tools/flaresolverr/FlareSolverr.exe

Default env values:
FLARESOLVERR_ENABLED=true
FLARESOLVERR_AUTO_START=true
FLARESOLVERR_URL=http://127.0.0.1:8191/v1
FLARESOLVERR_EXECUTABLE_PATH=tools/flaresolverr/FlareSolverr.exe

If the executable is missing, the app will try PATH, then Docker:
ghcr.io/flaresolverr/flaresolverr:latest
