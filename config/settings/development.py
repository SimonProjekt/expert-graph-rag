from .base import *  # noqa: F403
from apps.common.env import get_bool

DEBUG = get_bool("DEBUG", default=True)
