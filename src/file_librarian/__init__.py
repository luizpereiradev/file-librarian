"""file-librarian: keeps the folders you choose organized, starting with Downloads."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("file-librarian")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"

__all__ = ["__version__"]
