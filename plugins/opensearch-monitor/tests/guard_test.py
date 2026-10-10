#!/usr/bin/env python3
"""Tests for hooks/guard.py: runs it as a subprocess, as Claude Code does.

Run: python3 tests/guard_test.py -v   (from the plugin dir or the repo root)
"""
import json
import os
import subprocess
import sys
import unittest

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "hooks", "guard.py")
GUARD = os.path.normpath(GUARD)

HOST = "opensearch.example.com"
URL = "https://" + HOST
ALT = "search.acme.example"

PARSE = "could not parse"
CREDS = "credentials"
CONFIG = "config file"
WRITE = "non-GET"


def run_guard(stdin_text, osmon_url=URL, guard_hosts=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OSMON_")}
    if osmon_url is not None:
        env["OSMON_URL"] = osmon_url
    if guard_hosts is not None:
        env["OSMON_GUARD_HOSTS"] = guard_hosts
    p = subprocess.run([sys.executable, GUARD], input=stdin_text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                       timeout=10)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def hook_json(command):
    return json.dumps({"session_id": "test", "hook_event_name": "PreToolUse",
                       "tool_name": "Bash", "tool_input": {"command": command}})


class GuardCase(unittest.TestCase):
    def allow(self, command, **kw):
        code, out, err = run_guard(hook_json(command), **kw)
        self.assertEqual(code, 0, "expected allow for %r; stderr=%s" % (command, err))
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def deny(self, command, category, raw=False, **kw):
        code, out, err = run_guard(command if raw else hook_json(command), **kw)
        self.assertEqual(code, 2, "expected deny for %r" % (command,))
        self.assertEqual(out, "")
        self.assertIn(category, err)
        self.assertIn("osget", err)
        self.assertIn("human", err)


class AllowTests(GuardCase):
    def test_osget_bare(self):
        self.allow("osget _cluster/health")

    def test_osget_bare_with_body(self):
        self.allow("osget _plugins/_alerting/monitors/_search --body '{\"size\":0}'  # %s" % HOST)

    def test_osget_absolute_path_with_body_naming_host(self):
        self.allow("/opt/kit/skills/opensearch-health/scripts/osget "
                   "_plugins/_alerting/monitors/_search --body "
                   "'{\"query\":{\"match\":{\"host\":\"%s\"}}}' -X POST -d x" % HOST)

    def test_osget_piped_to_python(self):
        self.allow("osget _cat/indices?format=json | python3 -c "
                   "\"import json,sys; print(len(json.load(sys.stdin)))\"  # %s" % HOST)

    def test_unrelated_host_post(self):
        self.allow("curl https://example.org -X POST")

    def test_plain_get(self):
        self.allow("curl %s/_cluster/health" % URL)

    def test_get_with_common_flags(self):
        self.allow("curl -sS --max-time 10 -H 'Accept: application/json' '%s/_cat/nodes?v'" % URL)

    def test_explicit_get(self):
        self.allow("curl -X GET %s/_cluster/health" % URL)

    def test_head(self):
        self.allow("curl -I %s/" % URL)

    def test_head_method(self):
        self.allow("curl -X HEAD %s/" % URL)

    def test_get_flag_with_data(self):
        self.allow("curl -G -d q=1 %s/_search" % URL)

    def test_explicit_get_with_body(self):
        self.allow("curl -X GET %s/_search -d '{}'" % URL)

    def test_post_to_subdomain_is_other_host(self):
        self.allow("curl -X POST https://x.%s/a" % HOST)

    def test_post_to_suffixed_host_is_other_host(self):
        self.allow("curl -X POST https://%s.example.org/a" % HOST)

    def test_chained_post_to_other_host(self):
        self.allow("curl -X POST https://example.org/hook && curl %s/_cat/health" % URL)

    def test_quoted_semicolon_in_get(self):
        self.allow("curl '%s/_cat/indices?h=index;x'" % URL)

    def test_no_hosts_configured(self):
        self.allow("curl -u admin:x -X DELETE %s/idx" % URL, osmon_url=None)

    def test_grep_mentioning_host(self):
        self.allow("grep -rn '%s' . | head" % HOST)

    def test_commit_message_quoting_a_write(self):
        self.allow("git commit -m 'block curl -X POST %s/idx'" % URL)

    def test_httpie_get(self):
        self.allow("http %s/_cluster/health pretty==true" % URL)

    def test_wget_get(self):
        self.allow("wget -qO- %s/_cluster/health" % URL)

    def test_malformed_json_not_naming_host(self):
        code, out, err = run_guard("{not json at all")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_wrong_shape_json_not_naming_host(self):
        code, out, err = run_guard(json.dumps({"tool_input": None}))
        self.assertEqual((code, out, err), (0, "", ""))

    def test_unbalanced_quote_not_naming_host(self):
        self.allow("echo 'unterminated")


class CredentialTests(GuardCase):
    def test_u_separate(self):
        self.deny("curl -u admin:x %s/_cluster/health" % URL, CREDS)

    def test_u_attached(self):
        self.deny("curl -uadmin:x %s/_cluster/health" % URL, CREDS)

    def test_u_clustered(self):
        self.deny("curl -sSuadmin:x %s/_cluster/health" % URL, CREDS)

    def test_user_long(self):
        self.deny("curl --user admin:x %s/" % URL, CREDS)

    def test_authorization_header(self):
        self.deny("curl -H 'Authorization: Basic x' %s/" % URL, CREDS)

    def test_authorization_header_long_lowercase(self):
        self.deny("curl --header 'authorization:Bearer x' %s/" % URL, CREDS)

    def test_userinfo_in_url(self):
        self.deny("curl https://a:b@%s/" % HOST, CREDS)

    def test_oauth2_bearer(self):
        self.deny("curl --oauth2-bearer x %s/" % URL, CREDS)

    def test_wget_user_password(self):
        self.deny("wget --user=admin --password=x %s/" % URL, CREDS)

    def test_wget_http_password(self):
        self.deny("wget --http-password x %s/" % URL, CREDS)

    def test_wget_auth_header(self):
        self.deny("wget --header='Authorization: Basic x' %s/" % URL, CREDS)

    def test_httpie_auth(self):
        self.deny("http -a admin:x %s/" % URL, CREDS)

    def test_httpie_auth_header_item(self):
        self.deny("http %s/ 'Authorization:Basic x'" % URL, CREDS)


class WriteTests(GuardCase):
    def test_x_post(self):
        self.deny("curl -X POST %s/idx/_doc" % URL, WRITE)

    def test_xput_attached(self):
        self.deny("curl -XPUT %s/idx" % URL, WRITE)

    def test_clustered_x(self):
        self.deny("curl -sSXDELETE %s/idx" % URL, WRITE)

    def test_request_equals_delete(self):
        self.deny("curl --request=DELETE %s/idx" % URL, WRITE)

    def test_request_long(self):
        self.deny("curl --request PUT %s/_cluster/settings" % URL, WRITE)

    def test_x_patch_lowercase(self):
        self.deny("curl -X patch %s/idx" % URL, WRITE)

    def test_data(self):
        self.deny("curl %s/idx/_doc -d '{}'" % URL, WRITE)

    def test_data_attached(self):
        self.deny("curl %s/idx/_doc -d@body.json" % URL, WRITE)

    def test_data_binary(self):
        self.deny("curl --data-binary @f %s/_bulk" % URL, WRITE)

    def test_json(self):
        self.deny("curl --json '{}' %s/idx/_doc" % URL, WRITE)

    def test_form(self):
        self.deny("curl -F a=b %s/x" % URL, WRITE)

    def test_upload(self):
        self.deny("curl -T file %s/x" % URL, WRITE)

    def test_config_file(self):
        self.deny("curl -K req.cfg %s/x" % URL, CONFIG)

    def test_config_long_from_stdin(self):
        self.deny("printf 'request = DELETE' | curl --config - %s/idx" % URL, CONFIG)

    def test_wget_post_data(self):
        self.deny("wget --post-data=x https://%s/" % HOST, WRITE)

    def test_wget_method(self):
        self.deny("wget --method=DELETE %s/idx" % URL, WRITE)

    def test_httpie_put(self):
        self.deny("http PUT https://%s/x" % HOST, WRITE)

    def test_xh_delete(self):
        self.deny("xh delete %s/idx" % URL, WRITE)

    def test_httpie_data_item_implies_post(self):
        self.deny("http %s/idx/_doc a=b" % URL, WRITE)

    def test_httpie_piped_stdin_implies_post(self):
        self.deny("echo '{}' | http %s/idx/_doc" % URL, WRITE)

    def test_python_requests_post(self):
        self.deny("python3 -c \"import requests; requests.post('https://%s/x')\"" % HOST, WRITE)

    def test_node_fetch_delete(self):
        self.deny("node -e \"fetch('%s/idx', {method: 'DELETE'})\"" % URL, WRITE)

    def test_python_heredoc(self):
        self.deny("python3 - <<'EOF'\nimport requests\nrequests.put(\"%s/idx\")\nEOF" % URL, WRITE)

    def test_chained_and(self):
        self.deny("true && curl -X DELETE https://%s/i" % HOST, WRITE)

    def test_piped(self):
        self.deny("cat body.json | curl --data-binary @- %s/_bulk" % URL, WRITE)

    def test_semicolon_and_newline(self):
        self.deny("ls; echo hi\ncurl -X POST %s/x" % URL, WRITE)

    def test_quoted_semicolon_does_not_hide_flag(self):
        self.deny("curl -H ';' -X POST %s/x" % URL, WRITE)

    def test_command_substitution(self):
        self.deny("echo \"$(curl -X POST %s/x)\"" % URL, WRITE)

    def test_backticks(self):
        self.deny("echo `curl -X POST %s/x`" % URL, WRITE)

    def test_bash_c(self):
        self.deny("bash -lc 'curl -X DELETE %s/idx'" % URL, WRITE)

    def test_wrapper(self):
        self.deny("FOO=1 timeout 5 curl -X DELETE %s/idx" % URL, WRITE)

    def test_variable_url_in_chained_segment(self):
        self.deny("H=%s; curl -X DELETE \"$H/idx\"" % URL, WRITE)

    def test_line_continuation(self):
        self.deny("curl \\\n  -X DELETE \\\n  %s/idx" % URL, WRITE)


class HostConfigTests(GuardCase):
    def test_host_from_guard_hosts_only(self):
        self.deny("curl -X DELETE https://%s/idx" % ALT, WRITE, osmon_url=None,
                  guard_hosts=" %s , example.org" % ALT)

    def test_guard_hosts_alongside_url(self):
        self.deny("curl -X DELETE https://%s/idx" % ALT, WRITE, guard_hosts=ALT)

    def test_case_insensitive_host(self):
        self.deny("curl -X DELETE https://OpenSearch.Example.COM/idx", WRITE,
                  osmon_url="https://opensearch.EXAMPLE.com:9200")

    def test_bare_host_with_port(self):
        self.deny("curl -X DELETE %s:9200/idx" % HOST, WRITE)

    def test_url_without_scheme(self):
        self.deny("curl -X DELETE https://%s/idx" % HOST, WRITE, osmon_url=HOST)

    def test_bad_url_does_not_crash(self):
        code, _, err = run_guard(hook_json("curl -X DELETE https://%s/i" % ALT),
                                 osmon_url="http://[x", guard_hosts=ALT)
        self.assertEqual(code, 2, err)


class ConfigVariableTests(GuardCase):
    """A reference to $OSMON_URL / $OSMON_GUARD_HOSTS counts as naming the host."""

    def test_delete_via_var(self):
        self.deny('curl -X DELETE "$OSMON_URL/idx"', WRITE)

    def test_creds_via_braced_var(self):
        self.deny("curl -u a:b ${OSMON_URL}/x", CREDS)

    def test_braced_var_with_modifier(self):
        self.deny('curl -X PUT "${OSMON_URL%/}/idx"', WRITE)

    def test_guard_hosts_var(self):
        self.deny('curl -X DELETE "https://${OSMON_GUARD_HOSTS}/idx"', WRITE)

    def test_get_via_var(self):
        self.allow('curl "$OSMON_URL/_cluster/health"')

    def test_osget(self):
        self.allow("osget _cluster/health")

    def test_osget_no_env(self):
        self.allow("osget _cluster/health", osmon_url=None)

    def test_echo_var(self):
        self.allow('echo "$OSMON_URL"', osmon_url=None)

    def test_delete_via_var_no_env(self):
        self.deny('curl -X DELETE "$OSMON_URL/idx"', WRITE, osmon_url=None)

    def test_get_via_var_no_env(self):
        self.allow('curl "$OSMON_URL/_cluster/health"', osmon_url=None)

    def test_malformed_json_naming_var_no_env(self):
        self.deny('{"tool_input": {"command": "curl -X DELETE $OSMON_URL/i', PARSE,
                  raw=True, osmon_url=None)


class FailClosedTests(GuardCase):
    def test_malformed_json_naming_host(self):
        self.deny('{"tool_input": {"command": "curl https://%s/' % HOST, PARSE, raw=True)

    def test_wrong_shape_json_naming_host(self):
        self.deny(json.dumps({"tool_input": {"command": ["curl", URL]}}), PARSE, raw=True)

    def test_unbalanced_quotes_naming_host(self):
        self.deny("curl '%s/_cluster/health" % URL, PARSE)

    def test_non_utf8_stdin_naming_host(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OSMON_")}
        env["OSMON_URL"] = URL
        data = b'{"tool_input": {"command": "curl -X POST ' + URL.encode() + b'/x \xff"}}'
        p = subprocess.run([sys.executable, GUARD], input=data, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=10)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
