"""Real-Backend contract harness (IP-02 T-40, §16.3).

Owns the complete lifecycle needed to run the real Agent against the **actual** ASP.NET Core
Backend over loopback HTTP:

    real Agent (FastAPI lifespan + real httpx client)
        -> http://127.0.0.1:<dynamic port>
            -> actual WeaponDetection.Api process
                -> throwaway SQL Server database

Nothing here is production code and nothing here is imported by the fast default suite.

Design decisions (and why):

* **Subprocess, not ``WebApplicationFactory``.** The Backend's own integration tests use
  ``SqlServerApiHostFactory``, an in-process ``TestServer``. A ``TestServer`` exposes no TCP
  socket, so a Python ``httpx`` client cannot reach it — proving the *wire* contract requires a
  real listening port. We therefore launch the actual application as a subprocess.
* **The built DLL, not ``dotnet run``.** ``dotnet run`` launches the application as a *child* of
  the launcher process; terminating the launcher on Windows can orphan that child, which would
  violate the "no ``dotnet`` process remains" cleanup guarantee. Running the already-built
  ``WeaponDetection.Api.dll`` directly yields a single, reliably terminable process. It is the
  same application, built from the same solution, unmodified.
* **Port 0.** Kestrel is bound to ``http://127.0.0.1:0`` and announces the port it actually chose
  on stdout. This is race-free, unlike probing for a free port and then binding it. Loopback only
  — the host is never bound publicly.
* **Trusted authentication.** The database is reached through Windows integrated authentication,
  exactly as ``SqlServerApiHostFactory`` does, so no SQL password exists to hard-code, print, or
  commit.
* **``EFCORE_DESIGNTIME_CONNECTION``.** The Backend does not migrate on startup (its own test
  factory migrates *before* creating the host, because the startup Admin bootstrap needs a schema).
  Migrations are therefore applied explicitly via ``dotnet ef``, pointed at the throwaway database
  through the existing approved design-time seam. No Backend production change is required.

Secret safety: the Activation Key and the device shared secret are held in memory only, are never
placed on a command line or in an environment variable, and are scrubbed from any Backend log tail
surfaced in a failure message.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

# --- Environment gating -------------------------------------------------------------------------

#: Opt-in switch. Absent -> the suite skips with a precise reason (IP-02 T-40 permits this, exactly
#: as IP-01's SQL-Server-dependent suites skip). Present -> a misconfiguration is a hard failure, so
#: a contract test can never silently pass without having run.
RUN_FLAG = "WDA_RUN_BACKEND_CONTRACT_TESTS"

#: SQL Server instance hosting the throwaway database. Only the *instance* is configurable; the
#: database name is always generated. The default matches the instance the Backend's own
#: integration tests (``SqlServerApiHostFactory``) already hard-code, so a working .NET test setup
#: needs no extra configuration here. No credential is involved — authentication is trusted.
SQL_SERVER_VAR = "WDA_BACKEND_CONTRACT_SQL_SERVER"
DEFAULT_SQL_SERVER = r"localhost\SQLEXPRESS"

#: Bounded waits, in seconds.
BUILD_TIMEOUT = 600
MIGRATION_TIMEOUT = 300
STARTUP_TIMEOUT = 120
SHUTDOWN_TIMEOUT = 30
SQLCMD_TIMEOUT = 120


class ContractEnvironmentError(RuntimeError):
    """The contract suite was explicitly enabled but its environment is unusable.

    Raised (never swallowed into a skip) so an explicitly requested contract run fails loudly
    rather than quietly reporting success it did not earn.
    """


def contract_skip_reason() -> str | None:
    """Return why the contract suite cannot run, or ``None`` when it can."""
    if os.environ.get(RUN_FLAG) != "1":
        return f"{RUN_FLAG}=1 is not set (real-Backend contract suite is opt-in)"
    return None


def require_tooling() -> None:
    """Fail loudly when the enabled suite is missing external tooling."""
    for tool in ("dotnet", "sqlcmd"):
        if shutil.which(tool) is None:
            raise ContractEnvironmentError(
                f"{RUN_FLAG}=1 was set but {tool!r} is not on PATH; "
                "the real-Backend contract suite cannot run."
            )


def repository_root() -> Path:
    """Return the repository root (``agent/tests/support/`` -> three levels up)."""
    return Path(__file__).resolve().parents[3]


def backend_directory() -> Path:
    return repository_root() / "backend"


# --- Redaction ----------------------------------------------------------------------------------


def sanitize(text: str, secrets: Sequence[str]) -> str:
    """Scrub sensitive literals out of text destined for a failure message.

    Covers the two one-time values (Activation Key, shared secret) plus anything that looks like a
    SQL password, so a Backend log tail can be surfaced for diagnosis without leaking.
    """
    scrubbed = text
    for secret in secrets:
        if secret:
            scrubbed = scrubbed.replace(secret, "***REDACTED***")
    scrubbed = re.sub(r"(?i)(password|pwd)\s*=\s*[^;\r\n]*", r"\1=***REDACTED***", scrubbed)
    return scrubbed


def log_tail(path: Path, secrets: Sequence[str], *, lines: int = 40) -> str:
    """Return a sanitized tail of a Backend process log, for failure diagnostics only."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<log unavailable>"
    return sanitize("\n".join(content.splitlines()[-lines:]), secrets)


# --- Throwaway SQL Server database --------------------------------------------------------------


def _sqlcmd(server: str, query: str, *, database: str = "master") -> str:
    """Run a query through ``sqlcmd`` using trusted authentication.

    ``-C`` trusts the server certificate (the Backend's own connection strings set
    ``TrustServerCertificate=True`` for the same local-instance reason); ``-E`` selects integrated
    authentication, so no credential is ever passed.
    """
    result = subprocess.run(
        ["sqlcmd", "-S", server, "-E", "-C", "-d", database, "-b", "-Q", query],
        capture_output=True,
        text=True,
        timeout=SQLCMD_TIMEOUT,
    )
    if result.returncode != 0:
        # The query text is harmless (DDL against a generated database name) but the output is
        # sanitized anyway in case the server echoes connection details.
        raise ContractEnvironmentError(
            f"sqlcmd failed (exit {result.returncode}): {sanitize(result.stderr.strip(), [])}"
        )
    return result.stdout


@dataclass
class ThrowawayDatabase:
    """A uniquely named SQL Server database created, and dropped, by this harness alone.

    ``drop()`` refuses to touch a database this object did not create, so a development database
    can never be destroyed by a mistake here.
    """

    server: str
    name: str
    created: bool = False

    @property
    def connection_string(self) -> str:
        return (
            f"Server={self.server};Database={self.name};"
            "Trusted_Connection=True;TrustServerCertificate=True;"
        )

    def create(self) -> None:
        if _looks_like_a_protected_database(self.name):  # pragma: no cover - defensive
            raise ContractEnvironmentError(f"refusing to use non-throwaway database {self.name!r}")
        _sqlcmd(self.server, f"CREATE DATABASE [{self.name}];")
        self.created = True

    def exists(self) -> bool:
        output = _sqlcmd(
            self.server,
            f"SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name = N'{self.name}';",
        )
        return "1" in output.split()

    def drop(self) -> None:
        """Drop the database, forcing out any lingering connection first.

        Only ever drops a database this instance created.
        """
        if not self.created:
            return
        # SINGLE_USER WITH ROLLBACK IMMEDIATE evicts connections the Backend process may still be
        # holding through its EF connection pool, so the DROP cannot fail with "database in use".
        _sqlcmd(
            self.server,
            f"IF DB_ID(N'{self.name}') IS NOT NULL BEGIN "
            f"ALTER DATABASE [{self.name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"DROP DATABASE [{self.name}]; END",
        )
        self.created = False


_PROTECTED_DATABASES = {
    "master",
    "model",
    "msdb",
    "tempdb",
    "weapondetectiondev",
    "weapondetectionfresh",
    "weapondetectiondesigntime",
}


def _looks_like_a_protected_database(name: str) -> bool:
    return name.lower() in _PROTECTED_DATABASES or not name.startswith("WeaponDetectionContract_")


def new_throwaway_database(server: str) -> ThrowawayDatabase:
    return ThrowawayDatabase(server=server, name=f"WeaponDetectionContract_{uuid.uuid4().hex[:12]}")


# --- Build and migrations -----------------------------------------------------------------------


def build_backend() -> None:
    """Build the solution once, so the host can start from an already-built DLL."""
    result = subprocess.run(
        ["dotnet", "build", "WeaponDetection.slnx", "-c", "Debug", "--nologo"],
        cwd=backend_directory(),
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    if result.returncode != 0:
        raise ContractEnvironmentError(
            f"dotnet build failed (exit {result.returncode}):\n"
            f"{sanitize(result.stdout[-4000:], [])}"
        )


def apply_migrations(connection_string: str) -> None:
    """Apply the real EF Core migrations to the throwaway database.

    The Backend does not migrate at startup, and its startup Admin bootstrap requires an existing
    schema, so this must happen before the host is launched. ``EFCORE_DESIGNTIME_CONNECTION`` is
    the Backend's own design-time seam (``WeaponDetectionDbContextFactory``) — using it needs no
    production change and no user-secret.
    """
    env = dict(os.environ)
    env["EFCORE_DESIGNTIME_CONNECTION"] = connection_string
    result = subprocess.run(
        [
            "dotnet",
            "ef",
            "database",
            "update",
            "--project",
            "src/WeaponDetection.Infrastructure",
            "--startup-project",
            "src/WeaponDetection.Api",
            "--no-build",
        ],
        cwd=backend_directory(),
        env=env,
        capture_output=True,
        text=True,
        timeout=MIGRATION_TIMEOUT,
    )
    if result.returncode != 0:
        raise ContractEnvironmentError(
            f"dotnet ef database update failed (exit {result.returncode}):\n"
            f"{sanitize(result.stdout[-4000:] + result.stderr[-2000:], [])}"
        )


def _api_dll() -> Path:
    """Locate the built API assembly."""
    candidates = sorted(
        (backend_directory() / "src" / "WeaponDetection.Api" / "bin" / "Debug").glob(
            "*/WeaponDetection.Api.dll"
        )
    )
    if not candidates:
        raise ContractEnvironmentError(
            "WeaponDetection.Api.dll was not found; the Backend build did not produce an assembly."
        )
    return candidates[-1]


# --- Backend host -------------------------------------------------------------------------------

_LISTENING = re.compile(r"Now listening on:\s*(http://127\.0\.0\.1:(\d+))")


class BackendHost:
    """The actual Backend application, running on a loopback port against a throwaway database."""

    # Test-only Admin bootstrap and JWT configuration, mirroring the values the Backend's own
    # integration-test factory uses. These are not production credentials and unlock nothing beyond
    # a database that exists for the duration of one test run.
    ADMIN_IDENTIFIER = "contract-test-admin"
    ADMIN_PASSWORD = "Correct-Horse-Battery-Staple-1"
    JWT_ISSUER = "test-issuer"
    JWT_AUDIENCE = "test-audience"
    JWT_SIGNING_KEY = "k" * 32

    def __init__(self, connection_string: str, log_directory: Path) -> None:
        self._connection_string = connection_string
        self._log_path = log_directory / "backend-process.log"
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: object | None = None
        self.base_url: str = ""
        self.port: int = 0

    def start(self) -> None:
        dll = _api_dll()
        env = dict(os.environ)
        env.update(
            {
                # Port 0 -> Kestrel picks a free loopback port and reports it. Loopback only.
                "ASPNETCORE_URLS": "http://127.0.0.1:0",
                "ASPNETCORE_ENVIRONMENT": "Development",
                "ConnectionStrings__DefaultConnection": self._connection_string,
                "BootstrapAdmin__CredentialIdentifier": self.ADMIN_IDENTIFIER,
                "BootstrapAdmin__Password": self.ADMIN_PASSWORD,
                "Jwt__Issuer": self.JWT_ISSUER,
                "Jwt__Audience": self.JWT_AUDIENCE,
                "Jwt__SigningKey": self.JWT_SIGNING_KEY,
                "Jwt__AccessTokenLifetimeMinutes": "60",
            }
        )
        # Configuration travels through the environment, never argv, so nothing sensitive appears
        # in a process listing.
        handle = self._log_path.open("wb")
        self._log_handle = handle
        self._process = subprocess.Popen(
            ["dotnet", str(dll)],
            cwd=backend_directory() / "src" / "WeaponDetection.Api",
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            self.port = self._await_port()
            self.base_url = f"http://127.0.0.1:{self.port}"
            self._await_health()
        except Exception:
            self.stop()
            raise

    def _await_port(self) -> int:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ContractEnvironmentError(
                    f"Backend exited during startup with code {self._process.returncode}. "
                    f"Log tail:\n{log_tail(self._log_path, [])}"
                )
            match = _LISTENING.search(
                self._log_path.read_text(encoding="utf-8", errors="replace")
                if self._log_path.exists()
                else ""
            )
            if match:
                return int(match.group(2))
            time.sleep(0.2)
        raise ContractEnvironmentError(
            f"Backend did not report a listening port within {STARTUP_TIMEOUT}s. "
            f"Log tail:\n{log_tail(self._log_path, [])}"
        )

    def _await_health(self) -> None:
        """Poll the Backend's existing anonymous health route until it answers.

        No new endpoint is introduced for readiness — ``GET /api/v1/health`` already exists.
        """
        deadline = time.monotonic() + STARTUP_TIMEOUT
        last: str = "no response"
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ContractEnvironmentError(
                    f"Backend exited before becoming healthy (code {self._process.returncode}). "
                    f"Log tail:\n{log_tail(self._log_path, [])}"
                )
            try:
                response = httpx.get(f"{self.base_url}/api/v1/health", timeout=5)
                if response.status_code == 200:
                    return
                last = f"HTTP {response.status_code}"
            except httpx.HTTPError as error:
                last = type(error).__name__
            time.sleep(0.25)
        raise ContractEnvironmentError(
            f"Backend health check never succeeded ({last}). "
            f"Log tail:\n{log_tail(self._log_path, [])}"
        )

    def stop(self) -> None:
        """Terminate the Backend and release its log handle. Safe to call more than once."""
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Last resort only.
                process.kill()
                process.wait(timeout=SHUTDOWN_TIMEOUT)
        self._process = None
        handle = self._log_handle
        if handle is not None:
            handle.close()  # type: ignore[attr-defined]
            self._log_handle = None

    @property
    def log_path(self) -> Path:
        return self._log_path

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def port_is_released(self) -> bool:
        """True when nothing is listening on the host's port any more."""
        if not self.port:
            return True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            return probe.connect_ex(("127.0.0.1", self.port)) != 0


@contextmanager
def backend_host(connection_string: str, log_directory: Path) -> Iterator[BackendHost]:
    host = BackendHost(connection_string, log_directory)
    try:
        host.start()
        yield host
    finally:
        host.stop()


# --- Provisioning through the real, authenticated API --------------------------------------------


@dataclass(frozen=True)
class ProvisionedBranch:
    """A Branch/Camera/Device provisioned through the real API.

    ``activation_key`` is the complete one-time plaintext key. It is held in memory only; it is
    never logged, printed, placed in a filename, or written anywhere except the Agent's own
    protected key file.
    """

    branch_id: str
    activation_key: str


class AdminApiClient:
    """Drives the real, authenticated Backend API — the same routes the Dashboard uses.

    No test-only endpoint, seeding path, or domain bypass is introduced: the Branch (with its
    Camera, Device, and Activation Key) is created exactly as a real Admin would create it
    (FS-02 §5.1), which is what makes this a contract test rather than a fixture.
    """

    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30)
        self._token: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdminApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def login(self) -> None:
        response = self._client.post(
            "/api/v1/auth/login",
            json={
                "credentialIdentifier": BackendHost.ADMIN_IDENTIFIER,
                "password": BackendHost.ADMIN_PASSWORD,
            },
        )
        if response.status_code != 200:
            raise ContractEnvironmentError(
                f"Admin login failed with HTTP {response.status_code}; "
                "the Backend's bootstrap Admin was not provisioned as expected."
            )
        self._token = response.json()["data"]["token"]

    @property
    def _auth(self) -> dict[str, str]:
        if self._token is None:  # pragma: no cover - defensive
            raise ContractEnvironmentError("AdminApiClient.login() was not called")
        return {"Authorization": f"Bearer {self._token}"}

    def create_branch(self, name: str) -> ProvisionedBranch:
        """Create a Branch with one Camera; returns its id and the one-time Activation Key."""
        response = self._client.post(
            "/api/v1/branches",
            headers=self._auth,
            json={
                "name": name,
                "address": "1 Contract Test Way",
                "contactDetails": "contract-test@example.invalid",
                "cameras": [{"name": "Camera 1", "rtspUrl": "rtsp://camera.invalid:554/stream"}],
            },
        )
        if response.status_code != 201:
            raise ContractEnvironmentError(
                f"Branch creation failed with HTTP {response.status_code}"
            )
        data = response.json()["data"]
        return ProvisionedBranch(branch_id=data["branchId"], activation_key=data["activationKey"])

    def regenerate_activation_key(self, branch_id: str) -> str:
        """Regenerate the Activation Key through the real endpoint (FS-02 §5.3)."""
        response = self._client.post(
            f"/api/v1/devices/{branch_id}/activation-key/regenerate", headers=self._auth
        )
        if response.status_code != 200:
            raise ContractEnvironmentError(
                f"Activation-key regeneration failed with HTTP {response.status_code}"
            )
        return str(response.json()["data"]["activationKey"])

    def branch(self, branch_id: str) -> dict[str, object]:
        response = self._client.get(f"/api/v1/branches/{branch_id}", headers=self._auth)
        if response.status_code != 200:
            raise ContractEnvironmentError(f"Branch read failed with HTTP {response.status_code}")
        return dict(response.json()["data"])

    def device_summary(self, branch_id: str) -> dict[str, object]:
        """Return the Device summary embedded in the Branch view (status, DeviceId)."""
        return dict(self.branch(branch_id)["device"])  # type: ignore[arg-type]
