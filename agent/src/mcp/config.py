"""MCP server configuration management.

This module handles loading and validating MCP server configurations
from JSON files.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server.
    
    Attributes:
        server_id: Unique identifier for this server.
        command: Command to run the server (e.g., "uvx").
        args: Command line arguments (e.g., ["arxiv-mcp-server"]).
        env: Environment variables to set.
        disabled: Whether this server is disabled.
        timeout: Connection timeout in seconds.
        retry_count: Number of connection retry attempts.
        pool_min_size: Minimum connections in pool (default: 3).
        pool_max_size: Maximum connections in pool (default: 5).
    """
    
    server_id: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    timeout: float = 30.0
    retry_count: int = 3
    pool_min_size: int = 3
    pool_max_size: int = 5


class MCPConfigManager:
    """Manager for MCP server configurations.
    
    Handles loading, validating, and accessing MCP server configurations
    from a JSON configuration file.
    
    Attributes:
        config_path: Path to the configuration file.
        configs: Dictionary mapping server IDs to their configurations.
    """
    
    def __init__(self, config_path: str):
        """Initialize the configuration manager.
        
        Args:
            config_path: Path to the MCP servers configuration file.
        """
        self.config_path = Path(config_path)
        self.configs: Dict[str, MCPServerConfig] = {}
    
    def load_config(self) -> Dict[str, MCPServerConfig]:
        """Load and parse the configuration file.
        
        Returns:
            Dictionary mapping server IDs to MCPServerConfig objects.
            
        Note:
            Invalid configurations are logged and skipped.
            If the file doesn't exist, an empty dict is returned.
        """
        if not self.config_path.exists():
            logger.warning(f"MCP config file not found: {self.config_path}")
            return {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                raw_config = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in MCP config file: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error reading MCP config file: {e}")
            return {}
        
        mcp_servers = raw_config.get("mcpServers", {})
        
        for server_id, server_config in mcp_servers.items():
            errors = self.validate_config(server_config)
            if errors:
                logger.error(
                    f"Invalid config for server '{server_id}': {', '.join(errors)}"
                )
                continue
            
            self.configs[server_id] = MCPServerConfig(
                server_id=server_id,
                command=server_config["command"],
                args=server_config.get("args", []),
                env=server_config.get("env", {}),
                disabled=server_config.get("disabled", False),
                timeout=server_config.get("timeout", 30.0),
                retry_count=server_config.get("retryCount", 3),
                pool_min_size=server_config.get("poolMinSize", 3),
                pool_max_size=server_config.get("poolMaxSize", 5),
            )
        
        logger.info(f"Loaded {len(self.configs)} MCP server configurations")
        return self.configs
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate a server configuration.
        
        Args:
            config: Raw configuration dictionary to validate.
            
        Returns:
            List of validation error messages. Empty if valid.
        """
        errors = []
        
        # Required field: command
        if "command" not in config:
            errors.append("Missing required field 'command'")
        elif not isinstance(config["command"], str):
            errors.append("Field 'command' must be a string")
        elif not config["command"].strip():
            errors.append("Field 'command' cannot be empty")
        
        # Optional field: args (must be list of strings)
        if "args" in config:
            if not isinstance(config["args"], list):
                errors.append("Field 'args' must be a list")
            elif not all(isinstance(arg, str) for arg in config["args"]):
                errors.append("All items in 'args' must be strings")
        
        # Optional field: env (must be dict of strings)
        if "env" in config:
            if not isinstance(config["env"], dict):
                errors.append("Field 'env' must be a dictionary")
            elif not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in config["env"].items()
            ):
                errors.append("All keys and values in 'env' must be strings")
        
        # Optional field: disabled (must be boolean)
        if "disabled" in config and not isinstance(config["disabled"], bool):
            errors.append("Field 'disabled' must be a boolean")
        
        # Optional field: timeout (must be positive number)
        if "timeout" in config:
            if not isinstance(config["timeout"], (int, float)):
                errors.append("Field 'timeout' must be a number")
            elif config["timeout"] <= 0:
                errors.append("Field 'timeout' must be positive")
        
        # Optional field: retryCount (must be non-negative integer)
        if "retryCount" in config:
            if not isinstance(config["retryCount"], int):
                errors.append("Field 'retryCount' must be an integer")
            elif config["retryCount"] < 0:
                errors.append("Field 'retryCount' must be non-negative")
        
        return errors
    
    def get_enabled_configs(self) -> Dict[str, MCPServerConfig]:
        """Get only enabled server configurations.
        
        Returns:
            Dictionary of server configs where disabled=False.
        """
        return {
            server_id: config
            for server_id, config in self.configs.items()
            if not config.disabled
        }
    
    def get_config(self, server_id: str) -> Optional[MCPServerConfig]:
        """Get configuration for a specific server.
        
        Args:
            server_id: The server identifier.
            
        Returns:
            The server configuration, or None if not found.
        """
        return self.configs.get(server_id)
