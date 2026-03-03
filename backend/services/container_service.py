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
        # idle pooled containers
        self.idle_containers: List[docker.models.containers.Container] = []
        # initialized pool marker
        self._pool_initialized = False

        if not self._shutdown_registered:
            atexit.register(self.shutdown)
            self._shutdown_registered = True

    def _build_image_if_needed(self):
        try:
            self.client.images.get(AgentConfig.DOCKER_IMAGE_NAME)
        except docker.errors.ImageNotFound:
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
        base = max(1, int(getattr(AgentConfig, "DOCKER_POOL_SIZE", 1)))
        overflow = max(0, int(getattr(AgentConfig, "DOCKER_POOL_MAX_OVERFLOW", 0)))
        return base + overflow

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

        # Pre-warm base pool for lower latency and reliable parallel leasing
        base_size = max(1, int(getattr(AgentConfig, "DOCKER_POOL_SIZE", 1)))
        for _ in range(base_size):
            try:
                c = self._create_container()
                self.idle_containers.append(c)
            except Exception as e:
                logger.error(f"Failed to prewarm container pool: {e}")
                break

        self._pool_initialized = True
        logger.info(f"Container pool initialized with {len(self.idle_containers)} idle containers")

    def lease_container(self, account_id: int) -> Optional[str]:
        """
        Lease a container from the pool for a specific account.
        Returns container ID.
        """
        if not self.client:
            logger.error("Docker client not available.")
            return None

        with self._condition:
            self._initialize_pool_if_needed()

            existing = self.active_containers.get(account_id)
            if existing and self._is_container_healthy(existing):
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

    def release_container(self, account_id: int):
        """
        Return leased container to idle pool.
        Unhealthy containers are removed.
        """
        with self._condition:
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
            self._condition.notify_all()

    def _get_or_recover_container(self, account_id: int):
        with self._condition:
            container = self.active_containers.get(account_id)
            if container and self._is_container_healthy(container):
                return container

            # Try to recover by leasing a fresh one transparently
            self.active_containers.pop(account_id, None)
        leased_id = self.lease_container(account_id)
        if not leased_id:
            return None
        with self._condition:
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

    def shutdown(self):
        """
        Stops all active/idle containers and cleans stale managed containers.
        """
        logger.info("Shutting down ContainerService...")
        if not self.client:
            return

        with self._condition:
            for account_id, container in list(self.active_containers.items()):
                try:
                    container.remove(force=True)
                    logger.info(f"Removed active container {container.id[:12]} for account {account_id}")
                except Exception as e:
                    logger.error(f"Failed to remove active container for account {account_id}: {e}")
            self.active_containers.clear()

            for container in list(self.idle_containers):
                try:
                    container.remove(force=True)
                    logger.info(f"Removed idle container {container.id[:12]}")
                except Exception as e:
                    logger.error(f"Failed to remove idle container: {e}")
            self.idle_containers.clear()

            # Best-effort cleanup for managed containers that might be untracked
            try:
                leaked = self.client.containers.list(
                    all=True,
                    filters={"label": "open-alpha-arena-bench.managed=true"},
                )
                for container in leaked:
                    self._remove_container_quietly(container)
            except Exception as e:
                logger.warning(f"Failed to cleanup leaked managed containers: {e}")

            self._pool_initialized = False
            self._condition.notify_all()

        logger.info("ContainerService shutdown complete.")

