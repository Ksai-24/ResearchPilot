"""Python execution sandbox for BUG_FIX verification.

Runs model-generated code in a fresh subprocess with a hard timeout and a
temporary working directory, protected by multi-layer pre-execution AST security validation.
stdout+stderr are merged and returned.
"""
import ast
import asyncio
import os
import sys
import tempfile
from typing import Any, Dict, Optional

MAX_OUTPUT_CHARS = 4000

# Prohibited modules and system functions to prevent host compromise
_FORBIDDEN_MODULES = {
    "subprocess",
    "socket",
    "ctypes",
    "pty",
    "winreg",
    "webbrowser",
    "importlib",
    "builtins",
    "posix",
    "nt",
    "_posixsubprocess",
    "signal",
    "multiprocessing",
    "threading",
    "shutil",
    "code",
    "codeop",
    "runpy",
    "inspect",
    "mmap",
}

_FORBIDDEN_CALLS = {
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.mkdir",
    "os.rename",
    "os.replace",
    "os.environ",
    "shutil.rmtree",
    "sys.exit",
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "system",
    "popen",
}

_DANGEROUS_FUNCTIONS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "system",
    "popen",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
}

_FORBIDDEN_ATTRIBUTES = {
    "__subclasses__",
    "__globals__",
    "__code__",
    "__builtins__",
    "__bases__",
    "__mro__",
}


def validate_python_code(code: str) -> Optional[str]:
    """Perform pre-execution AST screening against dangerous primitives and sandbox escapes."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError in script: {e}"

    for node in ast.walk(tree):
        # 1. Screen Module Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                if root_pkg in _FORBIDDEN_MODULES:
                    return f"SecurityError: Prohibited module import '{alias.name}' in sandbox."
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                if root_pkg in _FORBIDDEN_MODULES:
                    return f"SecurityError: Prohibited import from '{node.module}' in sandbox."
                for alias in node.names:
                    call_name = f"{node.module}.{alias.name}"
                    if call_name in _FORBIDDEN_CALLS or alias.name in _DANGEROUS_FUNCTIONS:
                        return f"SecurityError: Prohibited import of '{alias.name}' from '{node.module}' in sandbox."

        # 2. Screen Function Calls
        elif isinstance(node, ast.Call):
            # Direct function call check (e.g. eval(...), system(...), exec(...))
            if isinstance(node.func, ast.Name):
                if node.func.id in _DANGEROUS_FUNCTIONS:
                    return f"SecurityError: Prohibited function execution '{node.func.id}()' in sandbox."
            # Module / Attribute method call check (e.g. os.system(...))
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                call_name = f"{node.func.value.id}.{node.func.attr}"
                if call_name in _FORBIDDEN_CALLS or node.func.attr in _DANGEROUS_FUNCTIONS:
                    return f"SecurityError: Prohibited system operation '{call_name}()' in sandbox."

        # 3. Screen Dangerous Attribute Introspection (e.g. obj.__subclasses__, obj.__globals__)
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRIBUTES:
                return f"SecurityError: Prohibited introspection attribute '{node.attr}' in sandbox."

    return None


async def run_python(code: str, timeout_s: int = 12) -> Dict[str, Any]:
    """Execute a Python script with AST security screening and timeout isolation."""
    # 1. AST security pre-flight check
    sec_err = validate_python_code(code)
    if sec_err:
        return {
            "ok": False,
            "returncode": 1,
            "output": f"[{sec_err}]",
            "security_violation": True,
        }

    # 2. Isolated execution in temp directory with sanitized environment
    env = os.environ.copy()
    # Strip API keys and sensitive tokens from sandbox process environment
    for sensitive_key in ("AIRA_API_KEY", "AIRA_SECONDARY_API_KEY", "MYSQL_PASSWORD", "SECRET_KEY"):
        env.pop(sensitive_key, None)

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "snippet.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            path,
            cwd=td,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "output": out.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS],
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {
                "ok": False,
                "returncode": None,
                "output": f"[timed out after {timeout_s}s — possible infinite loop]",
                "timed_out": True,
            }
