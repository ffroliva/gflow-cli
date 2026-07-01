"""gflow_cli.exceptions — standard alias for gflow_cli.errors.

Both module names resolve to the same set of public names.  Library
consumers may use whichever feels more idiomatic; ``gflow_cli.errors``
remains the canonical location.
"""

from __future__ import annotations

from gflow_cli.errors import EXIT_CODE_MAP as EXIT_CODE_MAP
from gflow_cli.errors import AuthExpiredError as AuthExpiredError
from gflow_cli.errors import AuthLoginTimeoutError as AuthLoginTimeoutError
from gflow_cli.errors import AuthMissingError as AuthMissingError
from gflow_cli.errors import BrowserSessionClosedError as BrowserSessionClosedError
from gflow_cli.errors import ConfigurationError as ConfigurationError
from gflow_cli.errors import ContentPolicyError as ContentPolicyError
from gflow_cli.errors import DataIntegrityError as DataIntegrityError
from gflow_cli.errors import DataMigrationError as DataMigrationError
from gflow_cli.errors import DataStoreError as DataStoreError
from gflow_cli.errors import FlowApiError as FlowApiError
from gflow_cli.errors import GFlowError as GFlowError
from gflow_cli.errors import NetworkError as NetworkError
from gflow_cli.errors import ProblemDetails as ProblemDetails
from gflow_cli.errors import RateLimitError as RateLimitError
from gflow_cli.errors import SecurityError as SecurityError
from gflow_cli.errors import TransportTimeoutError as TransportTimeoutError
from gflow_cli.errors import UiSelectorDriftError as UiSelectorDriftError
from gflow_cli.errors import UpscaleUnavailableError as UpscaleUnavailableError
from gflow_cli.errors import WafRejectionError as WafRejectionError
from gflow_cli.errors import WireFormatError as WireFormatError
