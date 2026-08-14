import docker
import logging
import os
import tarfile
import io
import threading
import time
import atexit
from typing import Optional, Tuple, Dict, List
from config.agent_config import AgentConfig

logger = logging.getLogger(__name__)

class ContainerService:
    _instance = None
    _instance_lock = threading.Lock()
    #: Docker HTTP calls during shutdown are bounded so bootstrap cleanup
    #: cannot hang forever waiting on the daemon.
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 10.0

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(ContainerService, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._shutdown_registered = False

        try:
            self.client = docker.from_env()
            self._build_image_if_needed()
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            self.client = None

        # account_id -> container_object (leased)
        self.active_containers: Dict[int, docker.models.containers.Container] = {}
        # Every adapter lease has an identity. A container may be shared by
        # overlapping leases from the same account, but it is returned to the
        # idle pool only after the final lease is released.
        self._active_lease_ids: Dict[int, set[str]] = {}
        # idle pooled containers
        self.idle_containers: List[docker.models.containers.Container] = []
        # initialized pool marker
        self._pool_initialized = False

        if not self._shutdown_registered:
            atexit.register(self.shutdown)
            self._shutdown_registered = True

    def _parse_image_reference(self, image_ref: str) -> Tuple[str, str]:
        """Split image reference into (repository, tag)."""
        value = (image_ref or "").strip()
        if not value:
            return "", "latest"
        if "@" in value:
            repository, digest = value.split("@", 1)
            return repository, digest

        last_slash = value.rfind("/")
        last_colon = value.rfind(":")
        if last_colon > last_slash:
            return value[:last_colon], value[last_colon + 1:]
        return value, "latest"

    def _normalize_repo_name(self, repository: str) -> str:
        repo = (repository or "").strip().lower()
        if repo.startswith("docker.io/"):
            repo = repo[len("docker.io/"):]
        if repo and "/" not in repo:
            repo = f"library/{repo}"
        return repo

    def _is_image_present(self, image_ref: str) -> bool:
        """
        Return True when a local image exists for the requested reference.
        Use a fallback strategy to avoid false negatives from direct `get`.
        """
        try:
            self.client.images.get(image_ref)
            return True
        except docker.errors.ImageNotFound:
            pass
        except Exception as e:
            logger.warning(f"Direct image lookup failed for {image_ref}: {e}")

        repository, expected_tag = self._parse_image_reference(image_ref)
        if not repository:
            return False

        try:
            candidates = self.client.images.list(name=repository)
        except Exception as e:
            logger.warning(f"Image list fallback failed for {image_ref}: {e}")
            return False

        normalized_expected_repo = self._normalize_repo_name(repository)
        expected_tag = (expected_tag or "latest").strip().lower()
        for image in candidates:
            for repo_tag in image.tags or []:
                found_repo, found_tag = self._parse_image_reference(repo_tag)
                if (found_tag or "").strip().lower() != expected_tag:
                    continue
                if self._normalize_repo_name(found_repo) == normalized_expected_repo:
                    return True
        return False

    def _build_image_if_needed(self):
        if self._is_image_present(AgentConfig.DOCKER_IMAGE_NAME):
            return

        logger.info(f"Image {AgentConfig.DOCKER_IMAGE_NAME} not found. Building...")
        dockerfile_path = AgentConfig.DOCKERFILE_PATH
        if os.path.exists(dockerfile_path):
            self.client.images.build(
                path=dockerfile_path,
                tag=AgentConfig.DOCKER_IMAGE_NAME,
                rm=True
            )
            logger.info("Image built successfully.")
        else:
            logger.error(f"Dockerfile path not found: {dockerfile_path}")

    def _pool_capacity(self) -> int:
        base = self._desired_base_pool_size()
        overflow = max(0, int(getattr(AgentConfig, "DOCKER_POOL_MAX_OVERFLOW", 0)))
        return base + overflow

    def _count_active_ai_accounts(self) -> int:
        """
        Count active AI accounts from DB.
        Fallback to configured baseline when DB is unavailable.
        """
        try:
            from database.connection import SessionLocal
            from database.models import Account

            active_values = ["true", "True", "1", "TRUE"]
            db = SessionLocal()
            try:
                count = (
                    db.query(Account)
                    .filter(
                        Account.account_type == "AI",
                        Account.is_active.in_(active_values),
                    )
                    .count()
                )
                return int(count)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to count active AI accounts for dynamic pool sizing: {e}")
            return max(1, int(getattr(AgentConfig, "DOCKER_POOL_SIZE", 1)))

    def _desired_base_pool_size(self) -> int:
        baseline = max(1, int(getattr(AgentConfig, "DOCKER_POOL_SIZE", 1)))
        dynamic_enabled = bool(
            getattr(AgentConfig, "DOCKER_POOL_DYNAMIC_BY_ACTIVE_ACCOUNTS", False)
        )
        if not dynamic_enabled:
            return baseline

        active_accounts = max(1, self._count_active_ai_accounts())
        desired = max(baseline, active_accounts)

        max_size = int(getattr(AgentConfig, "DOCKER_POOL_MAX_SIZE", 0) or 0)
        if max_size > 0:
            desired = min(desired, max_size)
        return desired

    def _sync_pool_size(self):
        """
        Dynamically grow/shrink idle pool to match desired base size.
        Never force-stop active leased containers.
        """
        if not self.client:
            return

        desired_total = self._desired_base_pool_size()
        tracked_count = len(self._tracked_container_ids())

        # Grow pool if too small
        while tracked_count < desired_total:
            try:
                c = self._create_container()
                self.idle_containers.append(c)
                tracked_count += 1
            except Exception as e:
                logger.error(f"Failed to grow container pool: {e}")
                break

        # Shrink only idle containers when too large
        while tracked_count > desired_total and self.idle_containers:
            c = self.idle_containers.pop()
            self._remove_container_quietly(c)
            tracked_count -= 1

    def _is_container_healthy(self, container) -> bool:
        try:
            container.reload()
            return container.status == "running"
        except Exception:
            return False

    def _remove_container_quietly(self, container):
        if not container:
            return
        try:
            container.remove(force=True)
        except Exception:
            pass

    def _container_labels(self) -> Dict[str, str]:
        return {
            "open-alpha-arena-bench.managed": "true",
            "open-alpha-arena-bench.component": "agent-sandbox",
        }

    def _create_container(self):
        return self.client.containers.run(
            AgentConfig.DOCKER_IMAGE_NAME,
            detach=True,
            network_disabled=True,
            mem_limit="512m",
            cpu_period=100000,
            cpu_quota=50000,  # 0.5 CPU
            labels=self._container_labels(),
        )

    def _tracked_container_ids(self) -> set:
        ids = set()
        for c in self.active_containers.values():
            if c:
                ids.add(c.id)
        for c in self.idle_containers:
            if c:
                ids.add(c.id)
        return ids

    def _initialize_pool_if_needed(self):
        if self._pool_initialized or not self.client:
            return

        # Cleanup stale managed containers from previous runs
        try:
            stale_managed = self.client.containers.list(
                all=True,
                filters={"label": "open-alpha-arena-bench.managed=true"},
            )
            for c in stale_managed:
                self._remove_container_quietly(c)
        except Exception as e:
            logger.warning(f"Failed to cleanup stale managed containers: {e}")

        # Also cleanup exited containers created from the sandbox image
        try:
            exited = self.client.containers.list(
                all=True,
                filters={"ancestor": AgentConfig.DOCKER_IMAGE_NAME, "status": "exited"},
            )
            for c in exited:
                self._remove_container_quietly(c)
        except Exception as e:
            logger.warning(f"Failed to cleanup exited sandbox containers: {e}")

        # Pre-warm dynamic base pool for lower latency and reliable parallel leasing
        base_size = self._desired_base_pool_size()
        for _ in range(base_size):
            try:
                c = self._create_container()
                self.idle_containers.append(c)
            except Exception as e:
                logger.error(f"Failed to prewarm container pool: {e}")
                break

        self._pool_initialized = True
        logger.info(f"Container pool initialized with {len(self.idle_containers)} idle containers")

    def lease_container(
        self, account_id: int, lease_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Lease a container from the pool for a specific account.
        Returns container ID.
        """
        if not self.client:
            logger.error("Docker client not available.")
            return None

        with self._condition:
            effective_lease_id = lease_id or "legacy-exclusive-lease"
            self._initialize_pool_if_needed()
            self._sync_pool_size()

            existing = self.active_containers.get(account_id)
            if existing and self._is_container_healthy(existing):
                self._active_lease_ids.setdefault(account_id, set()).add(
                    effective_lease_id
                )
                return existing.id
            if existing:
                self._remove_container_quietly(existing)
                self.active_containers.pop(account_id, None)

            timeout = max(
                1,
                int(getattr(AgentConfig, "DOCKER_POOL_LEASE_TIMEOUT_SECONDS", 30)),
            )
            deadline = time.time() + timeout

            while True:
                # Reuse idle container first
                while self.idle_containers:
                    candidate = self.idle_containers.pop()
                    if self._is_container_healthy(candidate):
                        self.active_containers[account_id] = candidate
                        self._active_lease_ids.setdefault(account_id, set()).add(
                            effective_lease_id
                        )
                        logger.info(
                            f"Leased pooled container {candidate.id[:12]} to account {account_id}"
                        )
                        return candidate.id
                    self._remove_container_quietly(candidate)

                # No idle containers, create if under capacity
                tracked_count = len(self._tracked_container_ids())
                if tracked_count < self._pool_capacity():
                    try:
                        new_container = self._create_container()
                        self.active_containers[account_id] = new_container
                        self._active_lease_ids.setdefault(account_id, set()).add(
                            effective_lease_id
                        )
                        logger.info(
                            f"Leased new container {new_container.id[:12]} to account {account_id}"
                        )
                        return new_container.id
                    except Exception as e:
                        logger.error(f"Failed to create new container for account {account_id}: {e}")

                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.error(f"Timed out leasing container for account {account_id}")
                    return None
                self._condition.wait(timeout=min(remaining, 1.0))

    def release_container(self, account_id: int, lease_id: Optional[str] = None):
        """
        Return leased container to idle pool.
        Unhealthy containers are removed.
        """
        with self._condition:
            effective_lease_id = lease_id or "legacy-exclusive-lease"
            active_lease_ids = self._active_lease_ids.get(account_id)
            if not active_lease_ids or effective_lease_id not in active_lease_ids:
                if lease_id is not None:
                    raise ValueError(
                        f"Unknown sandbox lease {lease_id!r} for account {account_id}"
                    )
                return
            active_lease_ids.remove(effective_lease_id)
            if active_lease_ids:
                return
            self._active_lease_ids.pop(account_id, None)
            container = self.active_containers.pop(account_id, None)
            if not container:
                return

            if self._is_container_healthy(container):
                self.idle_containers.append(container)
                logger.info(
                    f"Returned container {container.id[:12]} to pool for account {account_id}"
                )
            else:
                self._remove_container_quietly(container)
                logger.warning(
                    f"Removed unhealthy container for account {account_id} during release"
                )
            self._sync_pool_size()
            self._condition.notify_all()

    def _get_or_recover_container(self, account_id: int):
        # The whole recovery runs inside one ownership critical section (the
        # condition lock is re-entrant), so a concurrent release cannot
        # interleave between state inspection and the recovery lease. The
        # only place the lock can be dropped is lease_container's capacity
        # wait, which is why the final decision below is taken from the
        # *live* lease set instead of a snapshot taken before leasing.
        with self._condition:
            container = self.active_containers.get(account_id)
            if container and self._is_container_healthy(container):
                return container

            # Remove unhealthy container before re-leasing to avoid leaked
            # exited/dead containers occupying Docker resources.
            if container:
                self._remove_container_quietly(container)
                self.active_containers.pop(account_id, None)

            # Recover by leasing a fresh container under an internal lease.
            recovery_lease_id = f"internal-recovery-{time.monotonic_ns()}"
            leased_id = self.lease_container(account_id, recovery_lease_id)
            if not leased_id:
                return None

            lease_ids = self._active_lease_ids[account_id]
            lease_ids.discard(recovery_lease_id)
            if not lease_ids:
                # No external lease survived (either the account never had
                # one, or the last one was released while waiting for pool
                # capacity). Hold the container under the legacy exclusive
                # lease so the invariant "active container => non-empty
                # lease set" holds and the legacy release path still owns it.
                lease_ids.add("legacy-exclusive-lease")
            return self.active_containers.get(account_id)

    def execute_command(self, account_id: int, cmd: str) -> Tuple[int, str]:
        """
        Executes a command in the container.
        Returns (exit_code, output).
        """
        container = self._get_or_recover_container(account_id)
        if not container:
            return -1, "No active container found for this account."

        try:
            # cmd should be a string or list. 
            # To support shell features (pipes, etc.), run with bash -c
            # Use -u for python to prevent buffering, but also generally capture output
            # demux=False to merge stdout and stderr for a complete shell response
            exec_log = container.exec_run(
                ["/bin/bash", "-c", cmd],
                demux=False
            )
            exit_code = exec_log.exit_code
            
            # Output is bytes when demux=False
            output = exec_log.output.decode('utf-8', errors='replace') if exec_log.output else ""
            
            # Log execution details to docker_exec logger
            docker_logger = logging.getLogger("docker_exec")
            if output.strip():
                 docker_logger.info(f"Account: {account_id} | Cmd: {cmd} | Exit: {exit_code} | Output:\n{output.strip()}")
            else:
                 docker_logger.info(f"Account: {account_id} | Cmd: {cmd} | Exit: {exit_code} | Output: (Empty)")
            
            return exit_code, output
        except Exception as e:
            docker_logger = logging.getLogger("docker_exec")
            docker_logger.error(f"Account: {account_id} | Cmd: {cmd} | Error: {str(e)}")
            return -1, str(e)

    def read_file(self, account_id: int, file_path: str) -> str:
        container = self._get_or_recover_container(account_id)
        if not container:
            return "No active container found."

        try:
            # get_archive returns a tuple (iterator, stat)
            bits, stat = container.get_archive(file_path)
            file_content = b""
            for chunk in bits:
                file_content += chunk
            
            # Tar extraction to get actual file content
            with io.BytesIO(file_content) as f:
                with tarfile.open(fileobj=f) as tar:
                    # Assuming single file request, there should be one member
                    member = tar.next()
                    if not member.isfile():
                         return "Path is not a regular file."
                    f_extracted = tar.extractfile(member)
                    if f_extracted:
                        content = f_extracted.read()
                        decoded = content.decode('utf-8', errors='replace')
                        if len(decoded) > AgentConfig.MAX_READ_CHARS:
                            return decoded[:AgentConfig.MAX_READ_CHARS] + f"\n... (truncated, max {AgentConfig.MAX_READ_CHARS} chars)"
                        return decoded
            return "Empty file."
        except docker.errors.NotFound:
            return "File not found."
        except Exception as e:
            return f"Error reading file: {e}"

    def write_file(self, account_id: int, file_path: str, content: str) -> str:
        container = self._get_or_recover_container(account_id)
        if not container:
            return "No active container found."

        try:
            # Create tar in memory
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                data = content.encode('utf-8')
                info = tarfile.TarInfo(name=os.path.basename(file_path))
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            tar_stream.seek(0)

            dirname = os.path.dirname(file_path)
            if not dirname:
                dirname = "/workspace" # Default if no dir specified
            
            # Ensure directory exists
            container.exec_run(["mkdir", "-p", dirname])
            
            container.put_archive(
                path=dirname,
                data=tar_stream
            )
            return "Success"
        except Exception as e:
            return f"Error writing file: {e}"

    def _set_client_timeout(self, timeout: float):
        client = self.client
        previous = (
            getattr(client, "timeout", None),
            getattr(getattr(client, "api", None), "timeout", None),
        )
        if hasattr(client, "timeout"):
            client.timeout = timeout
        api = getattr(client, "api", None)
        if api is not None and hasattr(api, "timeout"):
            api.timeout = timeout
        return previous

    def _restore_client_timeout(self, previous) -> None:
        client_timeout, api_timeout = previous
        client = self.client
        if hasattr(client, "timeout"):
            client.timeout = client_timeout
        api = getattr(client, "api", None)
        if api is not None and hasattr(api, "timeout"):
            api.timeout = api_timeout

    def _remove_container_for_shutdown(self, container) -> None:
        """Remove a container; already-gone is the desired end state."""
        try:
            container.remove(force=True)
        except docker.errors.NotFound:
            return

    def shutdown(self):
        """Stop owned containers; fail if any tracked resource remains."""
        logger.info("Shutting down ContainerService...")
        if not self.client:
            return

        deadline = time.monotonic() + self.DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
        previous_timeout = self._set_client_timeout(
            self.DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
        )
        failures: List[str] = []
        try:
            with self._condition:
                def apply_remaining_timeout() -> None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("container shutdown deadline exceeded")
                    self._set_client_timeout(left)

                for account_id, container in list(self.active_containers.items()):
                    container_id = getattr(container, "id", str(container))
                    try:
                        apply_remaining_timeout()
                        self._remove_container_for_shutdown(container)
                    except Exception as exc:
                        failures.append(
                            f"active {account_id}/{container_id}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        logger.error(
                            "Failed to remove active container for account %s: %s",
                            account_id,
                            exc,
                        )
                    else:
                        self.active_containers.pop(account_id, None)
                        self._active_lease_ids.pop(account_id, None)
                        logger.info(
                            "Removed active container %s for account %s",
                            str(container_id)[:12],
                            account_id,
                        )

                remaining_idle = []
                for container in list(self.idle_containers):
                    container_id = getattr(container, "id", str(container))
                    try:
                        apply_remaining_timeout()
                        self._remove_container_for_shutdown(container)
                    except Exception as exc:
                        failures.append(
                            f"idle {container_id}: {type(exc).__name__}: {exc}"
                        )
                        remaining_idle.append(container)
                        logger.error(
                            "Failed to remove idle container %s: %s",
                            container_id,
                            exc,
                        )
                    else:
                        logger.info("Removed idle container %s", str(container_id)[:12])
                self.idle_containers = remaining_idle

                try:
                    apply_remaining_timeout()
                    tracked_ids = self._tracked_container_ids()
                    leaked = self.client.containers.list(
                        all=True,
                        filters={"label": "open-alpha-arena-bench.managed=true"},
                    )
                    for container in leaked:
                        container_id = getattr(container, "id", None)
                        if container_id in tracked_ids:
                            continue
                        try:
                            apply_remaining_timeout()
                            self._remove_container_for_shutdown(container)
                        except Exception as exc:
                            failures.append(
                                f"leaked {container_id}: {type(exc).__name__}: {exc}"
                            )
                except Exception as exc:
                    failures.append(
                        f"list leaked containers: {type(exc).__name__}: {exc}"
                    )
                    logger.warning(
                        "Failed to cleanup leaked managed containers: %s",
                        exc,
                    )

                if failures:
                    raise RuntimeError(
                        "container shutdown incomplete: " + "; ".join(failures)
                    )

                self._pool_initialized = False
                self._condition.notify_all()
        finally:
            self._restore_client_timeout(previous_timeout)

        logger.info("ContainerService shutdown complete.")
