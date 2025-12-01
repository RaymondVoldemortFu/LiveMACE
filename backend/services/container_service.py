import docker
import logging
import os
import tarfile
import io
from typing import Optional, Tuple
from config.agent_config import AgentConfig

logger = logging.getLogger(__name__)

class ContainerService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ContainerService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        try:
            self.client = docker.from_env()
            self._build_image_if_needed()
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            self.client = None
        
        self.active_containers = {}  # account_id -> container_object

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

    def lease_container(self, account_id: int) -> Optional[str]:
        """
        Starts a new container for the agent.
        Returns container ID.
        """
        if not self.client:
            logger.error("Docker client not available.")
            return None

        try:
            # Create a fresh container
            # Network disabled as per requirement
            container = self.client.containers.run(
                AgentConfig.DOCKER_IMAGE_NAME,
                detach=True,
                network_disabled=True,
                mem_limit="512m",
                cpu_period=100000,
                cpu_quota=50000, # 0.5 CPU
            )
            self.active_containers[account_id] = container
            logger.info(f"Leased container {container.id[:12]} to account {account_id}")
            return container.id
        except Exception as e:
            logger.error(f"Failed to lease container: {e}")
            return None

    def release_container(self, account_id: int):
        """
        Stops and removes the container for the agent.
        """
        container = self.active_containers.pop(account_id, None)
        if container:
            try:
                container.remove(force=True)
                logger.info(f"Released container {container.id[:12]} for account {account_id}")
            except Exception as e:
                logger.error(f"Failed to release container: {e}")

    def execute_command(self, account_id: int, cmd: str) -> Tuple[int, str]:
        """
        Executes a command in the container.
        Returns (exit_code, output).
        """
        container = self.active_containers.get(account_id)
        if not container:
            return -1, "No active container found for this account."

        try:
            # cmd should be a string or list. 
            # To support shell features (pipes, etc.), run with bash -c
            exec_log = container.exec_run(
                ["/bin/bash", "-c", cmd],
                demux=True # Return (stdout, stderr)
            )
            exit_code = exec_log.exit_code
            stdout = exec_log.output[0] if exec_log.output[0] else b""
            stderr = exec_log.output[1] if exec_log.output[1] else b""
            
            output = stdout.decode('utf-8', errors='replace') + stderr.decode('utf-8', errors='replace')
            
            # Log execution details to docker_exec logger
            docker_logger = logging.getLogger("docker_exec")
            docker_logger.info(f"Account: {account_id} | Cmd: {cmd} | Exit: {exit_code} | Output:\n{output.strip()}")
            
            return exit_code, output
        except Exception as e:
            docker_logger = logging.getLogger("docker_exec")
            docker_logger.error(f"Account: {account_id} | Cmd: {cmd} | Error: {str(e)}")
            return -1, str(e)

    def read_file(self, account_id: int, file_path: str) -> str:
        container = self.active_containers.get(account_id)
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
        container = self.active_containers.get(account_id)
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
        Stops all active containers and cleans up resources.
        """
        logger.info("Shutting down ContainerService...")
        for account_id, container in list(self.active_containers.items()):
            try:
                container.remove(force=True)
                logger.info(f"Removed container {container.id[:12]} for account {account_id}")
            except Exception as e:
                logger.error(f"Failed to remove container for account {account_id}: {e}")
        self.active_containers.clear()
        logger.info("ContainerService shutdown complete.")

