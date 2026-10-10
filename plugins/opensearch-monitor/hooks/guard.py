#!/usr/bin/env python3
"""PreToolUse guard for the Bash tool (opensearch-monitor plugin).

Blocks Bash commands that would send a non-GET request, or credentials on the
command line, to the configured OpenSearch host(s). Everything else is allowed.

Contract: hook JSON on stdin; exit 0 = allow (prints nothing); exit 2 = block,
with a one-paragraph reason on stderr. Python 3.8+, stdlib only, no network,
no file writes.

Hosts: hostname of OSMON_URL plus comma-separated OSMON_GUARD_HOSTS. A reference
to $OSMON_URL / $OSMON_GUARD_HOSTS in the command also counts as naming the host,
even when neither is set here. With no hosts and no such reference, everything
is allowed.
"""
import json
import os
import re
import shlex
import sys
from urllib.parse import urlsplit

TOOLS_CURL = {"curl"}
TOOLS_WGET = {"wget", "wget2"}
TOOLS_HTTPIE = {"http", "https", "xh", "xhs"}
PY_NODE_RE = re.compile(r"^(?:python[0-9.]*|node|nodejs)$")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
REPARSE_ALL_ARGS = {"eval", "ssh", "watch"}
WRAPPERS = {"sudo", "env", "time", "nohup", "exec", "command", "builtin",
            "nice", "timeout", "stdbuf", "caffeinate", "!"}
SAFE_METHODS = {"GET", "HEAD"}

# curl options that take a value (short options may be clustered: -sSXPOST).
CURL_SHORT_WITH_ARG = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
CURL_LONG_WITH_ARG = {
    "--abstract-unix-socket", "--alt-svc", "--aws-sigv4", "--cacert", "--capath",
    "--cert", "--cert-type", "--ciphers", "--config", "--connect-timeout",
    "--connect-to", "--continue-at", "--cookie", "--cookie-jar", "--crlfile",
    "--curves", "--data", "--data-ascii", "--data-binary", "--data-raw",
    "--data-urlencode", "--delegation", "--dns-interface", "--dns-ipv4-addr",
    "--dns-ipv6-addr", "--dns-servers", "--doh-url", "--dump-header", "--ech",
    "--egd-file", "--engine", "--etag-compare", "--etag-save",
    "--expect100-timeout", "--form", "--form-string", "--ftp-account",
    "--ftp-alternative-to-user", "--ftp-method", "--ftp-port",
    "--happy-eyeballs-timeout-ms", "--haproxy-clientip", "--header",
    "--hostpubmd5", "--hostpubsha256", "--hsts", "--interface", "--ip-tos",
    "--ipfs-gateway", "--json", "--keepalive-cnt", "--keepalive-time", "--key",
    "--key-type", "--krb", "--libcurl", "--limit-rate", "--local-port",
    "--login-options", "--mail-auth", "--mail-from", "--mail-rcpt",
    "--max-filesize", "--max-redirs", "--max-time", "--netrc-file", "--noproxy",
    "--oauth2-bearer", "--output", "--output-dir", "--parallel-max", "--pass",
    "--pinnedpubkey", "--preproxy", "--proto", "--proto-default",
    "--proto-redir", "--proxy", "--proxy-cacert", "--proxy-capath",
    "--proxy-cert", "--proxy-cert-type", "--proxy-ciphers", "--proxy-crlfile",
    "--proxy-header", "--proxy-key", "--proxy-key-type", "--proxy-pass",
    "--proxy-pinnedpubkey", "--proxy-service-name", "--proxy-tls13-ciphers",
    "--proxy-tlsauthtype", "--proxy-tlspassword", "--proxy-tlsuser",
    "--proxy-user", "--proxy1.0", "--pubkey", "--quote", "--random-file",
    "--range", "--rate", "--referer", "--request", "--request-target",
    "--resolve", "--retry", "--retry-delay", "--retry-max-time",
    "--sasl-authzid", "--service-name", "--socks4", "--socks4a", "--socks5",
    "--socks5-gssapi-service", "--socks5-hostname", "--speed-limit",
    "--speed-time", "--stderr", "--telnet-option", "--tftp-blksize",
    "--time-cond", "--tls-max", "--tls13-ciphers", "--tlsauthtype",
    "--tlspassword", "--tlsuser", "--trace", "--trace-ascii", "--trace-config",
    "--unix-socket", "--upload-file", "--url", "--url-query", "--user",
    "--user-agent", "--variable", "--write-out",
}
CURL_BODY_OPTS = {"-d", "-F", "-T", "--data", "--data-raw", "--data-binary",
                  "--data-urlencode", "--data-ascii", "--json", "--form",
                  "--form-string", "--upload-file"}

HTTPIE_SHORT_WITH_ARG = set("aAopPs")
HTTPIE_LONG_WITH_ARG = {
    "--auth", "--auth-type", "--session", "--session-read-only", "--output",
    "--verify", "--cert", "--cert-key", "--cert-key-pass", "--proxy",
    "--timeout", "--print", "--pretty", "--style", "--max-redirects",
    "--default-scheme", "--format-options", "--response-charset",
    "--response-mime", "--max-headers", "--ssl", "--ciphers", "--boundary",
    "--raw", "--history-print",
}
HTTPIE_ITEM_SEPS = (":=@", "=@", "==", ":=", "=", "@", ":")

AUTH_HEADER_RE = re.compile(r"^\s*authorization\s*:", re.I)
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SUBST_RE = re.compile(r"\$\(([^()]*)\)")
BACKTICK_RE = re.compile(r"`([^`]*)`")
WGETRC_RE = re.compile(
    r"^\s*(post_?data|post_?file|body_?data|body_?file|method|user|password|"
    r"http_?user|http_?password|header)\s*=", re.I)
CODE_WRITE_RES = [
    re.compile(r"requests\.(post|put|delete|patch)\b", re.I),
    re.compile(r"\.(post|put|delete|patch)\s*\(", re.I),
    re.compile(r"method\s*[=:]\s*['\"]?(POST|PUT|DELETE|PATCH)\b", re.I),
    re.compile(r"\.request\(\s*['\"](POST|PUT|DELETE|PATCH)\b", re.I),
    re.compile(r"get_method\s*=", re.I),
    re.compile(r"urlopen\(.*data\s*=", re.I | re.S),
    re.compile(r"Request\(.*data\s*=", re.S),
]

MAX_DEPTH = 4

KIND_WRITE = "write"
KIND_CREDS = "creds"
KIND_PARSE = "parse"
KIND_CONFIG = "config"

# A reference to the kit's own config variables counts as naming the host,
# whether or not they are set in the guard's environment.
VAR_RE = re.compile(r"\$\{?(?:OSMON_URL|OSMON_GUARD_HOSTS)\b")
NEVER_RE = re.compile(r"(?!x)x")


class Deny(Exception):
    def __init__(self, kind, detail):
        Exception.__init__(self, detail)
        self.kind = kind
        self.detail = detail


def reason(kind, detail):
    prefix = "opensearch-monitor guard blocked this Bash command: "
    if kind == KIND_CREDS:
        return (prefix + "it passes credentials on the command line to the "
                "OpenSearch cluster host (" + detail + "), which exposes them in "
                "process lists and shell history. Use `osget <path>` instead: it "
                "reads credentials from the configured source over stdin and only "
                "sends GET. If you need a request osget cannot make, do not work "
                "around this guard; give the human the exact command so they can "
                "run it themselves.")
    if kind == KIND_CONFIG:
        return (prefix + "it passes a curl config file to the OpenSearch cluster "
                "host (" + detail + "). The guard cannot see the options inside a "
                "config file (method, body, credentials), so it refuses it. Use "
                "`osget <path>` instead (GET only, `--body '<json>'` for search "
                "bodies), or give the human the exact command so they can run it "
                "themselves.")
    if kind == KIND_PARSE:
        return (prefix + "the guard could not parse it (" + detail + ") and it "
                "names the OpenSearch cluster host, so it is blocked to keep "
                "cluster access read-only. Rewrite it as a simple command, "
                "preferably `osget <path>` (GET only, `--body '<json>'` for search "
                "bodies), or give the human the exact command so they can run it "
                "themselves.")
    return (prefix + "it would send a non-GET request to the OpenSearch cluster "
            "host (" + detail + "). Cluster access from this session is "
            "read-only. For reads use `osget <path>` (GET only, `--body '<json>'` "
            "for search bodies). If a write is really needed, do not retry or "
            "work around this guard; give the human the exact command so they "
            "can run it themselves.")


# ---------------------------------------------------------------- hosts

def host_of(value):
    v = value.strip()
    if not v:
        return None
    probe = v if "://" in v else "//" + v
    try:
        host = urlsplit(probe).hostname
    except ValueError:
        host = None
    if not host:
        m = re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/]*@)?\[?([^/:?#\s\]]+)", v)
        host = m.group(1) if m else None
    if not host:
        return None
    host = host.strip().strip(".").lower()
    return host or None


def configured_hosts(env):
    hosts = []
    url = env.get("OSMON_URL", "")
    if url.strip():
        h = host_of(url)
        if h:
            hosts.append(h)
    for item in env.get("OSMON_GUARD_HOSTS", "").split(","):
        h = host_of(item)
        if h and h not in hosts:
            hosts.append(h)
    return hosts


class Ctx(object):
    def __init__(self, hosts):
        if hosts:
            alt = "|".join(re.escape(h) for h in sorted(hosts, key=len, reverse=True))
            tail = r")(?![A-Za-z0-9_-]|\.[A-Za-z0-9-])"
            self.host_re = re.compile(r"(?<![A-Za-z0-9_.-])(?:" + alt + tail, re.I)
            self.userinfo_re = re.compile(r"(?:^|//)[^\s/@?#]+@(?:" + alt + tail, re.I)
        else:
            self.host_re = NEVER_RE
            self.userinfo_re = NEVER_RE

    def names_host(self, text):
        return bool(self.host_re.search(text) or VAR_RE.search(text))


# ---------------------------------------------------------------- parsing

def split_segments(cmd):
    """Quote-aware split on ; & | newline ( ). Returns [(separator_before, text)].

    Quoted separators stay inside their word. Raises ValueError on unbalanced
    quotes.
    """
    segs = []
    buf = []
    sep = ""
    quote = None
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 2
                continue
            if c == '"':
                quote = None
            i += 1
            continue
        if c == "\\":
            buf.append(cmd[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in ";&|\n()":
            j = i
            while j < n and cmd[j] in ";&|\n() \t":
                j += 1
            ops = cmd[i:j].replace(" ", "").replace("\t", "")
            segs.append((sep, "".join(buf)))
            buf = []
            # a lone "|" (not "||") means the next segment's stdin is piped
            sep = "|" if ops == "|" else ops
            i = j
            continue
        buf.append(c)
        i += 1
    if quote:
        raise ValueError("unbalanced %s quote" % ("single" if quote == "'" else "double"))
    segs.append((sep, "".join(buf)))
    return [(s, t) for (s, t) in segs if t.strip()]


def base(word):
    w = word.strip("`")
    return w.rsplit("/", 1)[-1]


def program_index(words):
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        if ASSIGN_RE.match(w):
            i += 1
            continue
        b = base(w)
        if b in WRAPPERS:
            i += 1
            while i < n and words[i].startswith("-"):
                i += 1
            if b == "timeout" and i < n and words[i][:1].isdigit():
                i += 1
            continue
        return i
    return None


def parse_opts(args, short_with_arg, long_with_arg, expand_prefix=False):
    """Return (opts, positionals); opts is a list of (name, value-or-None)."""
    opts = []
    pos = []
    i = 0
    n = len(args)
    end = False
    while i < n:
        a = args[i]
        i += 1
        if end or a == "-" or not a.startswith("-"):
            pos.append(a)
            continue
        if a == "--":
            end = True
            continue
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if expand_prefix and name.startswith("--expand-"):
                name = "--" + name[len("--expand-"):]
            if eq:
                opts.append((name, val))
            elif name in long_with_arg:
                opts.append((name, args[i] if i < n else ""))
                i += 1
            else:
                opts.append((name, None))
            continue
        j = 1
        while j < len(a):
            c = a[j]
            j += 1
            if c in short_with_arg:
                if j < len(a):
                    val = a[j:]
                else:
                    val = args[i] if i < n else ""
                    i += 1
                opts.append(("-" + c, val))
                break
            opts.append(("-" + c, None))
    return opts, pos


# ---------------------------------------------------------------- per tool

def check_userinfo(args, ctx):
    for a in args:
        if ctx.userinfo_re.search(a):
            raise Deny(KIND_CREDS, "user:password@host in the URL")


def check_curl(args, ctx):
    opts, _ = parse_opts(args, CURL_SHORT_WITH_ARG, CURL_LONG_WITH_ARG, expand_prefix=True)
    explicit_get = False
    for name, val in opts:
        if name in ("-X", "--request"):
            if (val or "").strip().upper() not in SAFE_METHODS:
                raise Deny(KIND_WRITE, "curl %s %s" % (name, val))
            explicit_get = True
        elif name in ("-G", "--get"):
            explicit_get = True
    for name, val in opts:
        if name in ("-u", "--user"):
            raise Deny(KIND_CREDS, "curl %s" % name)
        if name == "--oauth2-bearer":
            raise Deny(KIND_CREDS, "curl --oauth2-bearer")
        if name in ("-H", "--header") and val and AUTH_HEADER_RE.match(val):
            raise Deny(KIND_CREDS, "curl %s 'Authorization: ...'" % name)
        if name in ("-K", "--config"):
            raise Deny(KIND_CONFIG, "curl %s" % name)
    check_userinfo(args, ctx)
    if not explicit_get:
        for name, _ in opts:
            if name in CURL_BODY_OPTS:
                raise Deny(KIND_WRITE, "curl %s without -G or -X GET makes curl "
                                       "send POST/PUT" % name)


def check_wget(args, ctx):
    n = len(args)
    for i, a in enumerate(args):
        name, eq, val = a.partition("=")
        if not eq:
            val = args[i + 1] if i + 1 < n else ""
        if name in ("--post-data", "--post-file", "--body-data", "--body-file"):
            raise Deny(KIND_WRITE, "wget %s" % name)
        if name == "--method" and val.strip().upper() not in SAFE_METHODS:
            raise Deny(KIND_WRITE, "wget --method %s" % val)
        if name in ("--user", "--password", "--http-user", "--http-password"):
            raise Deny(KIND_CREDS, "wget %s" % name)
        if name == "--header" and AUTH_HEADER_RE.match(val):
            raise Deny(KIND_CREDS, "wget --header 'Authorization: ...'")
        if a in ("-e", "--execute") and WGETRC_RE.match(val):
            raise Deny(KIND_WRITE, "wget %s %s" % (a, val))
        if name == "--execute" and eq and WGETRC_RE.match(val):
            raise Deny(KIND_WRITE, "wget %s" % a)
        if a.startswith("-e") and len(a) > 2 and WGETRC_RE.match(a[2:]):
            raise Deny(KIND_WRITE, "wget %s" % a)
    check_userinfo(args, ctx)


def httpie_item_kind(item):
    best = None
    for sep in HTTPIE_ITEM_SEPS:
        idx = item.find(sep)
        if idx < 0:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(sep) > len(best[1])):
            best = (idx, sep)
    if best is None:
        return None, item
    return best[1], item[:best[0]]


def check_httpie(args, ctx, stdin_piped):
    opts, pos = parse_opts(args, HTTPIE_SHORT_WITH_ARG, HTTPIE_LONG_WITH_ARG)
    ignore_stdin = False
    for name, _ in opts:
        if name in ("-a", "--auth"):
            raise Deny(KIND_CREDS, "httpie %s" % name)
        if name in ("-I", "--ignore-stdin"):
            ignore_stdin = True
    method = None
    items = pos[1:]
    if len(pos) >= 2 and re.match(r"^[A-Za-z]+$", pos[0]):
        method = pos[0].upper()
        items = pos[2:]
        if method not in SAFE_METHODS and method != "OPTIONS":
            raise Deny(KIND_WRITE, "httpie method %s" % pos[0])
    has_data = False
    for item in items:
        sep, key = httpie_item_kind(item)
        if sep == ":" and key.strip().lower() == "authorization":
            raise Deny(KIND_CREDS, "httpie Authorization: header item")
        if sep in (":=@", "=@", ":=", "=", "@"):
            has_data = True
    check_userinfo(args, ctx)
    if method is None:
        if has_data:
            raise Deny(KIND_WRITE, "httpie request items without an explicit GET "
                                   "make httpie send POST")
        if any(name == "--raw" for name, _ in opts):
            raise Deny(KIND_WRITE, "httpie --raw without an explicit GET makes "
                                   "httpie send POST")
        if stdin_piped and not ignore_stdin:
            raise Deny(KIND_WRITE, "httpie with piped or redirected stdin and no "
                                   "explicit GET sends POST")


# ---------------------------------------------------------------- commands

def check_segment(words, raw, sep, ctx, depth):
    # Nested shells: re-analyse the command string they run.
    for i, w in enumerate(words):
        b = base(w)
        if b in SHELLS:
            for j in range(i + 1, len(words)):
                o = words[j]
                if o.startswith("-") and not o.startswith("--") and "c" in o[1:]:
                    if j + 1 < len(words):
                        check_command(words[j + 1], ctx, depth + 1)
                    break
        elif b in REPARSE_ALL_ARGS and i + 1 < len(words):
            check_command(" ".join(words[i + 1:]), ctx, depth + 1)

    p = program_index(words)
    if p is None or base(words[p]) == "osget":
        return
    # The whole command names a guarded host; a segment counts when it names
    # it too, or could reach it through a variable or substitution.
    if not (ctx.names_host(raw) or "$" in raw or "`" in raw):
        return
    for i, w in enumerate(words):
        b = base(w)
        args = words[i + 1:]
        if b in TOOLS_CURL:
            check_curl(args, ctx)
            return
        if b in TOOLS_WGET:
            check_wget(args, ctx)
            return
        if b in TOOLS_HTTPIE:
            stdin_piped = sep == "|" or any(a.startswith("<") for a in args)
            check_httpie(args, ctx, stdin_piped)
            return
        if PY_NODE_RE.match(b):
            code = " ".join(args)
            for rx in CODE_WRITE_RES:
                m = rx.search(code)
                if m:
                    raise Deny(KIND_WRITE, "%s code matching %r" % (b, m.group(0)[:40]))
            return


def check_command(cmd, ctx, depth=0):
    """Raise Deny if cmd would write to, or send credentials to, a guarded host.

    depth 0 is the hook's command (already known to name a guarded host);
    deeper levels are strings run by nested shells or substitutions, where
    parse failures are ignored because the outer command parsed.
    """
    if depth > MAX_DEPTH:
        return
    strict = depth == 0
    cmd = cmd.replace("\\\n", " ")
    for inner in SUBST_RE.findall(cmd) + BACKTICK_RE.findall(cmd):
        check_command(inner, ctx, depth + 1)
    try:
        segs = split_segments(cmd)
    except ValueError as e:
        if strict:
            raise Deny(KIND_PARSE, str(e))
        return
    has_py_node = False
    for sep, raw in segs:
        try:
            words = shlex.split(raw, posix=True)
        except ValueError as e:
            if strict:
                raise Deny(KIND_PARSE, str(e))
            continue
        if not words:
            continue
        if any(PY_NODE_RE.match(base(w)) for w in words):
            has_py_node = True
        check_segment(words, raw, sep, ctx, depth)
    # Heredocs and multi-line scripts: crude scan of the whole string.
    if has_py_node and strict:
        for rx in CODE_WRITE_RES:
            m = rx.search(cmd)
            if m:
                raise Deny(KIND_WRITE, "python/node code matching %r" % m.group(0)[:40])


def decide(raw, env):
    """Return (exit_code, stderr_text) for raw hook input and environment."""
    hosts = configured_hosts(env)
    if not hosts and not VAR_RE.search(raw):
        return 0, ""
    ctx = Ctx(hosts)
    try:
        cmd = json.loads(raw)["tool_input"]["command"]
    except Exception:
        cmd = None
    if not isinstance(cmd, str):
        if ctx.names_host(raw):
            return 2, reason(KIND_PARSE, "hook input is not JSON with a "
                                         "tool_input.command string")
        return 0, ""
    if not ctx.names_host(cmd):
        return 0, ""
    try:
        check_command(cmd, ctx)
    except Deny as d:
        return 2, reason(d.kind, d.detail)
    return 0, ""


def main():
    raw = ""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        code, msg = decide(raw, os.environ)
    except Exception as e:  # never exit 1 (non-blocking); fail closed on the host
        code, msg = 0, ""
        try:
            low = raw.lower()
            if VAR_RE.search(raw):
                code = 2
                msg = reason(KIND_PARSE, "internal error: %s" % type(e).__name__)
            names = [os.environ.get("OSMON_URL", "")] + \
                os.environ.get("OSMON_GUARD_HOSTS", "").split(",")
            for item in names:
                h = host_of(item) if item.strip() else None
                if h and h in low:
                    code = 2
                    msg = reason(KIND_PARSE, "internal error: %s" % type(e).__name__)
                    break
        except Exception:
            code, msg = 2, reason(KIND_PARSE, "internal error")
    if code == 2:
        sys.stderr.write(msg + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
