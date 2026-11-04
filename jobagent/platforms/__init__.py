"""Registry of supported platforms.

To add a site (Indeed, Foundit, Y Combinator, Hiring.cafe, Himalayas...), subclass
`Platform` in a new module and add it to PLATFORMS.
"""

from __future__ import annotations

from .amazon import Amazon
from .ashby import Ashby
from .base import Platform
from .greenhouse import Greenhouse
from .lever import Lever
from .linkedin import LinkedIn
from .naukri import Naukri
from .successfactors import SuccessFactors
from .wellfound import Wellfound
from .workday import Workday

PLATFORMS: dict[str, type[Platform]] = {
    cls.name: cls
    for cls in (Greenhouse, Lever, Ashby, Workday, SuccessFactors, Amazon, LinkedIn, Naukri, Wellfound)
}

__all__ = ["PLATFORMS", "Platform"]
