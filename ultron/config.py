"""ULTRON Configuration Loader"""
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    name: str = "qwen2.5-1.5b-instruct-q4_k_m"
    repo_id: str = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    filename: str = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    path: str = "models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    n_gpu_layers: int = -1
    n_ctx: int = 4096
    n_batch: int = 512
    n_threads: int = 8
    verbose: bool = False


@dataclass
class VoiceConfig:
    enabled: bool = False
    push_to_talk: bool = True
    wake_word: bool = False
    stt_engine: str = "whisper.cpp"
    stt_model: str = "base.en"
    tts_engine: str = "piper"
    tts_voice: str = "en_US-lessac-medium"
    sample_rate: int = 16000
    channels: int = 1


@dataclass
class WorkspaceConfig:
    default_path: Optional[str] = None
    read_only_default: bool = True
    max_file_size_mb: int = 50
    supported_extensions: list = field(default_factory=lambda: [
        ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".csv", ".pdf", ".docx", ".xlsx", ".log"
    ])
    chunk_size: int = 512
    chunk_overlap: int = 64
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedder_device: str = "auto"
    retriever_top_k: int = 5
    retriever_threshold: float = 0.3


@dataclass
class ResourcesConfig:
    max_vram_gb: float = 3.5
    max_ram_gb: float = 12.0
    max_context_tokens: int = 4096
    max_concurrent_inferences: int = 1
    unload_idle_after_seconds: int = 300


@dataclass
class PermissionsConfig:
    levels: list = field(default_factory=lambda: [
        "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])
    default_level: str = "READ"
    require_confirmation: list = field(default_factory=lambda: [
        "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])


@dataclass
class SecurityConfig:
    audit_log_enabled: bool = True
    audit_log_path: str = "logs/audit.log"
    workspace_isolation: bool = True
    cloud_transmission_blocked: bool = True
    allowed_external_domains: list = field(default_factory=list)


@dataclass
class PCControlConfig:
    allowed_apps: list = field(default_factory=lambda: [
        "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
    ])
    allowed_paths: list = field(default_factory=lambda: [
        "C:\\Users\\%USERNAME%\\Documents",
        "C:\\Users\\%USERNAME%\\Desktop",
        "C:\\Ultron"
    ])


@dataclass
class MemoryConfig:
    general_db: str = "memory/general.db"
    workspace_db_template: str = "workspaces/{workspace_id}/memory.db"
    max_entries_per_workspace: int = 10000
    retention_days: int = 90


@dataclass
class UltronConfig:
    name: str = "ULTRON"
    version: str = "0.1.0"
    data_dir: str = ".ultron"
    log_level: str = "INFO"
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    pc_control: PCControlConfig = field(default_factory=PCControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


# Factory functions for nested YAML structures
def create_voice_config(data: dict) -> VoiceConfig:
    """Create VoiceConfig from YAML data, handling nested structures"""
    stt = data.get('stt', {})
    tts = data.get('tts', {})
    
    return VoiceConfig(
        enabled=data.get('enabled', False),
        push_to_talk=data.get('push_to_talk', True),
        wake_word=data.get('wake_word', False),
        stt_engine=stt.get('engine', 'whisper.cpp') if isinstance(stt, dict) else 'whisper.cpp',
        stt_model=stt.get('model', 'base.en') if isinstance(stt, dict) else 'base.en',
        tts_engine=tts.get('engine', 'piper') if isinstance(tts, dict) else 'piper',
        tts_voice=tts.get('voice', 'en_US-lessac-medium') if isinstance(tts, dict) else 'en_US-lessac-medium',
        sample_rate=data.get('sample_rate', 16000),
        channels=data.get('channels', 1),
    )


def create_workspace_config(data: dict) -> WorkspaceConfig:
    """Create WorkspaceConfig from YAML data, handling nested structures"""
    parser = data.get('parser', {})
    embedder = data.get('embedder', {})
    retriever = data.get('retriever', {})
    
    return WorkspaceConfig(
        default_path=data.get('default_path'),
        read_only_default=data.get('read_only_default', True),
        max_file_size_mb=data.get('max_file_size_mb', 50),
        supported_extensions=data.get('supported_extensions', [
            ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
            ".csv", ".pdf", ".docx", ".xlsx", ".log"
        ]),
        chunk_size=parser.get('chunk_size', 512) if isinstance(parser, dict) else 512,
        chunk_overlap=parser.get('chunk_overlap', 64) if isinstance(parser, dict) else 64,
        embedder_model=embedder.get('model', 'sentence-transformers/all-MiniLM-L6-v2') if isinstance(embedder, dict) else 'sentence-transformers/all-MiniLM-L6-v2',
        embedder_device=embedder.get('device', 'auto') if isinstance(embedder, dict) else 'auto',
        retriever_top_k=retriever.get('top_k', 5) if isinstance(retriever, dict) else 5,
        retriever_threshold=retriever.get('similarity_threshold', 0.3) if isinstance(retriever, dict) else 0.3,
    )


def create_resources_config(data: dict) -> ResourcesConfig:
    return ResourcesConfig(
        max_vram_gb=data.get('max_vram_gb', 3.5),
        max_ram_gb=data.get('max_ram_gb', 12.0),
        max_context_tokens=data.get('max_context_tokens', 4096),
        max_concurrent_inferences=data.get('max_concurrent_inferences', 1),
        unload_idle_after_seconds=data.get('unload_idle_after_seconds', 300),
    )


def create_permissions_config(data: dict) -> PermissionsConfig:
    return PermissionsConfig(
        levels=data.get('levels', [
            "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
        default_level=data.get('default_level', 'READ'),
        require_confirmation=data.get('require_confirmation', [
            "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
    )


def create_security_config(data: dict) -> SecurityConfig:
    return SecurityConfig(
        audit_log_enabled=data.get('audit_log_enabled', True),
        audit_log_path=data.get('audit_log_path', 'logs/audit.log'),
        workspace_isolation=data.get('workspace_isolation', True),
        cloud_transmission_blocked=data.get('cloud_transmission_blocked', True),
        allowed_external_domains=data.get('allowed_external_domains', []),
    )


def create_pc_control_config(data: dict) -> PCControlConfig:
    return PCControlConfig(
        allowed_apps=data.get('allowed_apps', [
            "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
        ]),
        allowed_paths=data.get('allowed_paths', [
            "C:\\Users\\%USERNAME%\\Documents",
            "C:\\Users\\%USERNAME%\\Desktop",
            "C:\\Ultron"
        ]),
    )


def create_memory_config(data: dict) -> MemoryConfig:
    return MemoryConfig(
        general_db=data.get('general_db', 'memory/general.db'),
        workspace_db_template=data.get('workspace_db_template', 'workspaces/{workspace_id}/memory.db'),
        max_entries_per_workspace=data.get('max_entries_per_workspace', 10000),
        retention_days=data.get('retention_days', 90),
    )


def create_model_config(data: dict) -> ModelConfig:
    return ModelConfig(
        name=data.get('name', 'qwen2.5-1.5b-instruct-q4_k_m'),
        repo_id=data.get('repo_id', 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'),
        filename=data.get('filename', 'qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        path=data.get('path', 'models/qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        n_gpu_layers=data.get('n_gpu_layers', -1),
        n_ctx=data.get('n_ctx', 4096),
        n_batch=data.get('n_batch', 512),
        n_threads=data.get('n_threads', 8),
        verbose=data.get('verbose', False),
    )


@dataclass
class WorkspaceConfig:
    default_path: Optional[str] = None
    read_only_default: bool = True
    max_file_size_mb: int = 50
    supported_extensions: list = field(default_factory=lambda: [
        ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".csv", ".pdf", ".docx", ".xlsx", ".log"
    ])
    chunk_size: int = 512
    chunk_overlap: int = 64
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedder_device: str = "cpu"
    retriever_top_k: int = 5
    retriever_threshold: float = 0.3


@dataclass
class ResourcesConfig:
    max_vram_gb: float = 3.5
    max_ram_gb: float = 12.0
    max_context_tokens: int = 4096
    max_concurrent_inferences: int = 1
    unload_idle_after_seconds: int = 300


@dataclass
class PermissionsConfig:
    levels: list = field(default_factory=lambda: [
        "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])
    default_level: str = "READ"
    require_confirmation: list = field(default_factory=lambda: [
        "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])


@dataclass
class SecurityConfig:
    audit_log_enabled: bool = True
    audit_log_path: str = "logs/audit.log"
    workspace_isolation: bool = True
    cloud_transmission_blocked: bool = True
    allowed_external_domains: list = field(default_factory=list)


@dataclass
class PCControlConfig:
    allowed_apps: list = field(default_factory=lambda: [
        "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
    ])
    allowed_paths: list = field(default_factory=lambda: [
        "C:\\Users\\%USERNAME%\\Documents",
        "C:\\Users\\%USERNAME%\\Desktop",
        "C:\\Ultron"
    ])


@dataclass
class MemoryConfig:
    general_db: str = "memory/general.db"
    workspace_db_template: str = "workspaces/{workspace_id}/memory.db"
    max_entries_per_workspace: int = 10000
    retention_days: int = 90


@dataclass
class UltronConfig:
    name: str = "ULTRON"
    version: str = "0.1.0"
    data_dir: str = ".ultron"
    log_level: str = "INFO"
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    pc_control: PCControlConfig = field(default_factory=PCControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


# Factory functions for nested YAML structures
def create_voice_config(data: dict) -> VoiceConfig:
    """Create VoiceConfig from YAML data, handling nested structures"""
    stt = data.get('stt', {})
    tts = data.get('tts', {})
    
    return VoiceConfig(
        enabled=data.get('enabled', False),
        push_to_talk=data.get('push_to_talk', True),
        wake_word=data.get('wake_word', False),
        stt_engine=stt.get('engine', 'whisper.cpp') if isinstance(stt, dict) else 'whisper.cpp',
        stt_model=stt.get('model', 'base.en') if isinstance(stt, dict) else 'base.en',
        tts_engine=tts.get('engine', 'piper') if isinstance(tts, dict) else 'piper',
        tts_voice=tts.get('voice', 'en_US-lessac-medium') if isinstance(tts, dict) else 'en_US-lessac-medium',
        sample_rate=data.get('sample_rate', 16000),
        channels=data.get('channels', 1),
    )


def create_workspace_config(data: dict) -> WorkspaceConfig:
    """Create WorkspaceConfig from YAML data, handling nested structures"""
    parser = data.get('parser', {})
    embedder = data.get('embedder', {})
    retriever = data.get('retriever', {})
    
    return WorkspaceConfig(
        default_path=data.get('default_path'),
        read_only_default=data.get('read_only_default', True),
        max_file_size_mb=data.get('max_file_size_mb', 50),
        supported_extensions=data.get('supported_extensions', [
            ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
            ".csv", ".pdf", ".docx", ".xlsx", ".log"
        ]),
        chunk_size=parser.get('chunk_size', 512) if isinstance(parser, dict) else 512,
        chunk_overlap=parser.get('chunk_overlap', 64) if isinstance(parser, dict) else 64,
        embedder_model=embedder.get('model', 'sentence-transformers/all-MiniLM-L6-v2') if isinstance(embedder, dict) else 'sentence-transformers/all-MiniLM-L6-v2',
        embedder_device=embedder.get('device', 'cpu') if isinstance(embedder, dict) else 'cpu',
        retriever_top_k=retriever.get('top_k', 5) if isinstance(retriever, dict) else 5,
        retriever_threshold=retriever.get('similarity_threshold', 0.3) if isinstance(retriever, dict) else 0.3,
    )


def create_resources_config(data: dict) -> ResourcesConfig:
    return ResourcesConfig(
        max_vram_gb=data.get('max_vram_gb', 3.5),
        max_ram_gb=data.get('max_ram_gb', 12.0),
        max_context_tokens=data.get('max_context_tokens', 4096),
        max_concurrent_inferences=data.get('max_concurrent_inferences', 1),
        unload_idle_after_seconds=data.get('unload_idle_after_seconds', 300),
    )


def create_permissions_config(data: dict) -> PermissionsConfig:
    return PermissionsConfig(
        levels=data.get('levels', [
            "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
        default_level=data.get('default_level', 'READ'),
        require_confirmation=data.get('require_confirmation', [
            "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
    )


def create_security_config(data: dict) -> SecurityConfig:
    return SecurityConfig(
        audit_log_enabled=data.get('audit_log_enabled', True),
        audit_log_path=data.get('audit_log_path', 'logs/audit.log'),
        workspace_isolation=data.get('workspace_isolation', True),
        cloud_transmission_blocked=data.get('cloud_transmission_blocked', True),
        allowed_external_domains=data.get('allowed_external_domains', []),
    )


def create_pc_control_config(data: dict) -> PCControlConfig:
    return PCControlConfig(
        allowed_apps=data.get('allowed_apps', [
            "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
        ]),
        allowed_paths=data.get('allowed_paths', [
            "C:\\Users\\%USERNAME%\\Documents",
            "C:\\Users\\%USERNAME%\\Desktop",
            "C:\\Ultron"
        ]),
    )


def create_memory_config(data: dict) -> MemoryConfig:
    return MemoryConfig(
        general_db=data.get('general_db', 'memory/general.db'),
        workspace_db_template=data.get('workspace_db_template', 'workspaces/{workspace_id}/memory.db'),
        max_entries_per_workspace=data.get('max_entries_per_workspace', 10000),
        retention_days=data.get('retention_days', 90),
    )


def create_model_config(data: dict) -> ModelConfig:
    return ModelConfig(
        name=data.get('name', 'qwen2.5-1.5b-instruct-q4_k_m'),
        repo_id=data.get('repo_id', 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'),
        filename=data.get('filename', 'qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        path=data.get('path', 'models/qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        n_gpu_layers=data.get('n_gpu_layers', 0),
        n_ctx=data.get('n_ctx', 4096),
        n_batch=data.get('n_batch', 512),
        n_threads=data.get('n_threads', 8),
        verbose=data.get('verbose', False),
    )


@dataclass
class WorkspaceConfig:
    default_path: Optional[str] = None
    read_only_default: bool = True
    max_file_size_mb: int = 50
    supported_extensions: list = field(default_factory=lambda: [
        ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".csv", ".pdf", ".docx", ".xlsx", ".log"
    ])
    chunk_size: int = 512
    chunk_overlap: int = 64
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedder_device: str = "cpu"
    retriever_top_k: int = 5
    retriever_threshold: float = 0.3


@dataclass
class ResourcesConfig:
    max_vram_gb: float = 3.5
    max_ram_gb: float = 12.0
    max_context_tokens: int = 4096
    max_concurrent_inferences: int = 1
    unload_idle_after_seconds: int = 300


@dataclass
class PermissionsConfig:
    levels: list = field(default_factory=lambda: [
        "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])
    default_level: str = "READ"
    require_confirmation: list = field(default_factory=lambda: [
        "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])


@dataclass
class SecurityConfig:
    audit_log_enabled: bool = True
    audit_log_path: str = "logs/audit.log"
    workspace_isolation: bool = True
    cloud_transmission_blocked: bool = True
    allowed_external_domains: list = field(default_factory=list)


@dataclass
class PCControlConfig:
    allowed_apps: list = field(default_factory=lambda: [
        "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
    ])
    allowed_paths: list = field(default_factory=lambda: [
        "C:\\Users\\%USERNAME%\\Documents",
        "C:\\Users\\%USERNAME%\\Desktop",
        "C:\\Ultron"
    ])


@dataclass
class MemoryConfig:
    general_db: str = "memory/general.db"
    workspace_db_template: str = "workspaces/{workspace_id}/memory.db"
    max_entries_per_workspace: int = 10000
    retention_days: int = 90


@dataclass
class UltronConfig:
    name: str = "ULTRON"
    version: str = "0.1.0"
    data_dir: str = ".ultron"
    log_level: str = "INFO"
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    pc_control: PCControlConfig = field(default_factory=PCControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


# Factory functions for nested YAML structures
def create_voice_config(data: dict) -> VoiceConfig:
    """Create VoiceConfig from YAML data, handling nested structures"""
    stt = data.get('stt', {})
    tts = data.get('tts', {})
    
    return VoiceConfig(
        enabled=data.get('enabled', False),
        push_to_talk=data.get('push_to_talk', True),
        wake_word=data.get('wake_word', False),
        stt_engine=stt.get('engine', 'whisper.cpp') if isinstance(stt, dict) else 'whisper.cpp',
        stt_model=stt.get('model', 'base.en') if isinstance(stt, dict) else 'base.en',
        tts_engine=tts.get('engine', 'piper') if isinstance(tts, dict) else 'piper',
        tts_voice=tts.get('voice', 'en_US-lessac-medium') if isinstance(tts, dict) else 'en_US-lessac-medium',
        sample_rate=data.get('sample_rate', 16000),
        channels=data.get('channels', 1),
    )


def create_workspace_config(data: dict) -> WorkspaceConfig:
    """Create WorkspaceConfig from YAML data, handling nested structures"""
    parser = data.get('parser', {})
    embedder = data.get('embedder', {})
    retriever = data.get('retriever', {})
    
    return WorkspaceConfig(
        default_path=data.get('default_path'),
        read_only_default=data.get('read_only_default', True),
        max_file_size_mb=data.get('max_file_size_mb', 50),
        supported_extensions=data.get('supported_extensions', [
            ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
            ".csv", ".pdf", ".docx", ".xlsx", ".log"
        ]),
        chunk_size=parser.get('chunk_size', 512) if isinstance(parser, dict) else 512,
        chunk_overlap=parser.get('chunk_overlap', 64) if isinstance(parser, dict) else 64,
        embedder_model=embedder.get('model', 'sentence-transformers/all-MiniLM-L6-v2') if isinstance(embedder, dict) else 'sentence-transformers/all-MiniLM-L6-v2',
        embedder_device=embedder.get('device', 'cpu') if isinstance(embedder, dict) else 'cpu',
        retriever_top_k=retriever.get('top_k', 5) if isinstance(retriever, dict) else 5,
        retriever_threshold=retriever.get('similarity_threshold', 0.3) if isinstance(retriever, dict) else 0.3,
    )


def create_resources_config(data: dict) -> ResourcesConfig:
    return ResourcesConfig(
        max_vram_gb=data.get('max_vram_gb', 3.5),
        max_ram_gb=data.get('max_ram_gb', 12.0),
        max_context_tokens=data.get('max_context_tokens', 4096),
        max_concurrent_inferences=data.get('max_concurrent_inferences', 1),
        unload_idle_after_seconds=data.get('unload_idle_after_seconds', 300),
    )


def create_permissions_config(data: dict) -> PermissionsConfig:
    return PermissionsConfig(
        levels=data.get('levels', [
            "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
        default_level=data.get('default_level', 'READ'),
        require_confirmation=data.get('require_confirmation', [
            "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
        ]),
    )


def create_security_config(data: dict) -> SecurityConfig:
    return SecurityConfig(
        audit_log_enabled=data.get('audit_log_enabled', True),
        audit_log_path=data.get('audit_log_path', 'logs/audit.log'),
        workspace_isolation=data.get('workspace_isolation', True),
        cloud_transmission_blocked=data.get('cloud_transmission_blocked', True),
        allowed_external_domains=data.get('allowed_external_domains', []),
    )


def create_pc_control_config(data: dict) -> PCControlConfig:
    return PCControlConfig(
        allowed_apps=data.get('allowed_apps', [
            "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
        ]),
        allowed_paths=data.get('allowed_paths', [
            "C:\\Users\\%USERNAME%\\Documents",
            "C:\\Users\\%USERNAME%\\Desktop",
            "C:\\Ultron"
        ]),
    )


def create_memory_config(data: dict) -> MemoryConfig:
    return MemoryConfig(
        general_db=data.get('general_db', 'memory/general.db'),
        workspace_db_template=data.get('workspace_db_template', 'workspaces/{workspace_id}/memory.db'),
        max_entries_per_workspace=data.get('max_entries_per_workspace', 10000),
        retention_days=data.get('retention_days', 90),
    )


def create_model_config(data: dict) -> ModelConfig:
    return ModelConfig(
        name=data.get('name', 'qwen2.5-1.5b-instruct-q4_k_m'),
        repo_id=data.get('repo_id', 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'),
        filename=data.get('filename', 'qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        path=data.get('path', 'models/qwen2.5-1.5b-instruct-q4_k_m.gguf'),
        n_gpu_layers=data.get('n_gpu_layers', 0),
        n_ctx=data.get('n_ctx', 4096),
        n_batch=data.get('n_batch', 512),
        n_threads=data.get('n_threads', 8),
        verbose=data.get('verbose', False),
    )


@dataclass
class WorkspaceConfig:
    default_path: Optional[str] = None
    read_only_default: bool = True
    max_file_size_mb: int = 50
    supported_extensions: list = field(default_factory=lambda: [
        ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".csv", ".pdf", ".docx", ".xlsx", ".log"
    ])
    chunk_size: int = 512
    chunk_overlap: int = 64
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedder_device: str = "cpu"
    retriever_top_k: int = 5
    retriever_threshold: float = 0.3


@dataclass
class ResourcesConfig:
    max_vram_gb: float = 3.5
    max_ram_gb: float = 12.0
    max_context_tokens: int = 4096
    max_concurrent_inferences: int = 1
    unload_idle_after_seconds: int = 300


@dataclass
class PermissionsConfig:
    levels: list = field(default_factory=lambda: [
        "READ", "SEARCH", "CREATE", "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])
    default_level: str = "READ"
    require_confirmation: list = field(default_factory=lambda: [
        "MODIFY", "DELETE", "EXECUTE", "EXTERNAL_TRANSMIT"
    ])


@dataclass
class SecurityConfig:
    audit_log_enabled: bool = True
    audit_log_path: str = "logs/audit.log"
    workspace_isolation: bool = True
    cloud_transmission_blocked: bool = True
    allowed_external_domains: list = field(default_factory=list)


@dataclass
class PCControlConfig:
    allowed_apps: list = field(default_factory=lambda: [
        "notepad.exe", "code.exe", "explorer.exe", "cmd.exe", "powershell.exe", "wt.exe"
    ])
    allowed_paths: list = field(default_factory=lambda: [
        "C:\\Users\\%USERNAME%\\Documents",
        "C:\\Users\\%USERNAME%\\Desktop",
        "C:\\Ultron"
    ])


@dataclass
class MemoryConfig:
    general_db: str = "memory/general.db"
    workspace_db_template: str = "workspaces/{workspace_id}/memory.db"
    max_entries_per_workspace: int = 10000
    retention_days: int = 90


@dataclass
class UltronConfig:
    name: str = "ULTRON"
    version: str = "0.1.0"
    data_dir: str = ".ultron"
    log_level: str = "INFO"
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    pc_control: PCControlConfig = field(default_factory=PCControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


class Config:
    _instance: Optional['Config'] = None
    _config: Optional[UltronConfig] = None
    _config_path: Optional[Path] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, config_path: Optional[str] = None) -> UltronConfig:
        if config_path is None:
            # Find project root (C:\Ultron) by looking for .ultron directory
            current = Path(__file__).parent
            while current != current.parent:
                if (current / ".ultron").exists():
                    config_path = current / ".ultron" / "config" / "config.yaml"
                    break
                current = current.parent
            else:
                config_path = Path("C:/Ultron/.ultron/config/config.yaml")
        else:
            config_path = Path(config_path)

        self._config_path = config_path

        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        ultron_data = data.get('ultron', {})
        # Also check top-level for sections (voice, resources, etc. may be at top level)
        self._config = UltronConfig(
            name=ultron_data.get('name', 'ULTRON'),
            version=ultron_data.get('version', '0.1.0'),
            data_dir=ultron_data.get('data_dir', '.ultron'),
            log_level=ultron_data.get('log_level', 'INFO'),
            resources=create_resources_config(data.get('resources', ultron_data.get('resources', {}))),
            model=create_model_config(data.get('model', ultron_data.get('model', {}))),
            voice=create_voice_config(data.get('voice', ultron_data.get('voice', {}))),
            workspace=create_workspace_config(data.get('workspace', ultron_data.get('workspace', {}))),
            permissions=create_permissions_config(data.get('permissions', ultron_data.get('permissions', {}))),
            security=create_security_config(data.get('security', ultron_data.get('security', {}))),
            pc_control=create_pc_control_config(data.get('pc_control', ultron_data.get('pc_control', {}))),
            memory=create_memory_config(data.get('memory', ultron_data.get('memory', {}))),
        )
        return self._config

    @property
    def config(self) -> UltronConfig:
        if self._config is None:
            self.load()
        return self._config

    def _get_project_root(self) -> Path:
        """Get project root from config path"""
        if self._config_path:
            return self._config_path.parent.parent.parent
        return Path(r"C:\Ultron")

    def get_model_path(self) -> Path:
        """Resolve model path relative to data_dir"""
        base = self._get_project_root()
        return base / self.config.data_dir / self.config.model.path

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve path relative to data_dir"""
        base = self._get_project_root()
        return base / self.config.data_dir / relative_path


# Global instance
config = Config()