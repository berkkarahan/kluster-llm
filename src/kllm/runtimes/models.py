"""Validated runtime identities shared by HTTP and signed node commands."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UPSTREAM = 'https://github.com/ggml-org/llama.cpp.git'
BACKENDS = ('auto', 'metal', 'cuda', 'rocm', 'vulkan', 'cpu')
PRESET_VERSION = 1


def repository_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use a public HTTPS Git repository without credentials or query parameters')
    if parsed.port not in (None, 443) or not parsed.path.strip('/') or any(p in ('.', '..') for p in parsed.path.split('/')):
        raise ValueError('Invalid public Git repository URL')
    path = parsed.path.rstrip('/')
    if not path.endswith('.git'):
        path += '.git'
    return urlunsplit(('https', parsed.hostname.lower(), path, '', ''))


def runtime_id(repository: str, commit: str) -> str:
    return hashlib.sha256((repository + '\n' + commit).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SourceRequest(Record):
    repository: str = UPSTREAM
    ref: str = Field(min_length=1, max_length=256)
    ref_type: str = Field(default='tag', pattern='^(tag|branch|commit)$')
    name: str = Field(default='', max_length=120)

    _repository = field_validator('repository')(repository_url)

    @field_validator('ref')
    @classmethod
    def ref_value(cls, value: str) -> str:
        if value.startswith('-') or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in '~^:?*[\\') or '..' in value or '@{' in value:
            raise ValueError('Use a valid Git tag, branch, or full commit')
        return value

    @model_validator(mode='after')
    def commit_ref(self):
        if self.ref_type == 'commit' and not re.fullmatch('[0-9a-fA-F]{40}', self.ref):
            raise ValueError('Commit references require the full 40-character SHA')
        return self


class RuntimeVersion(SourceRequest):
    commit: str = Field(pattern='^[0-9a-f]{40}$')
    runtime_id: str = Field(pattern='^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def identity(self):
        if self.runtime_id != runtime_id(self.repository, self.commit):
            raise ValueError('Runtime identity does not match repository and commit')
        return self


class BuildRequest(Record):
    runtime_id: str = Field(pattern='^[0-9a-f]{64}$')
    backend: str = Field(default='auto', pattern='^(auto|metal|cuda|rocm|vulkan|cpu)$')
    request_id: str = Field(pattern='^[0-9a-f-]{36}$')


class TargetRequest(Record):
    runtime_id: str = Field(pattern='^[0-9a-f]{64}$')
    expected_revision: int = Field(ge=0)
    request_id: str = Field(pattern='^[0-9a-f-]{36}$')


class RuntimeTarget(Record):
    version: RuntimeVersion
    revision: int = Field(gt=0)
    cluster_binding_id: str
    master_node_id: str


def body_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
