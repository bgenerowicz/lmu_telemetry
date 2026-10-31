# PyInstaller entry point (a script outside the packages, so imports are absolute).
import sys

from desktop.main import main

sys.exit(main())
