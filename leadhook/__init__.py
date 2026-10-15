from .celery import app as celery_app

__version__ = "0.1.0"
__all__ = ["celery_app", "__version__"]
