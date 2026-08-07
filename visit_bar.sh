#!/usr/bin/env sh
# Start the visit bar. Optional: pass minutes, e.g.  ./visit_bar.sh 25
# Linux/macOS counterpart of visit_bar.bat — picks the right port for this OS.
dir=$(dirname "$0")
case "$(uname -s)" in
  Darwin) exec python3 "$dir/visit_bar_mac.py" "$@" ;;
  *)      exec python3 "$dir/visit_bar_linux.py" "$@" ;;
esac
