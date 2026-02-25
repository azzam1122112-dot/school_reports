# reports/views/__init__.py
"""
Re-export every public view so that ``from reports import views``
followed by ``views.some_view`` keeps working exactly as before.
"""

from ._helpers import *          # noqa: F401,F403  – shared helpers + user_guide views
from .auth import *              # noqa: F401,F403
from .platform import *          # noqa: F401,F403
from .home import *              # noqa: F401,F403
from .reports import *           # noqa: F401,F403
from .achievements import *      # noqa: F401,F403
from .teachers import *          # noqa: F401,F403
from .tickets import *           # noqa: F401,F403
from .schools import *           # noqa: F401,F403
from .notifications import *     # noqa: F401,F403
from .subscriptions import *     # noqa: F401,F403
from .reporttypes import *       # noqa: F401,F403
from .api import *               # noqa: F401,F403
from .onboarding import *       # noqa: F401,F403
