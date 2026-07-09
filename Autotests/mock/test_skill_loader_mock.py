"""Mock variant of the filesystem skill loader (Issue #11).

Proves the *agent uses a loaded skill* end-to-end: a fixture SKILL.md is written into
the running container's skill root, and the mocked LLM response invokes
``(use-skill "<name>")`` and then sends a reply. The ``use-skill`` tool runs for real
inside the agent's PeTTa runtime, so ``skill_loader.get_skill_body`` returns the real
body (carrying a unique marker) into the agent's LAST_SKILL_USE_RESULTS — which we then
assert appears in history.

Docker-gated (like the other ``mock/`` tests): needs a started container.
Run: pytest test_skill_loader_mock.py -s
"""

from actions import act
from helpers import (
    Checker, dexec_root, make_prompt,
    wait_for_history_keyword, wait_for_skill_call,
)

# In-container default skill root (repo baked into the image, see profile/skills.yaml).
_SKILL_ROOT = "/PeTTa/repos/OmegaClaw-Core/skills"


def _install_fixture_skill(name, marker):
    """Write skills/<name>/SKILL.md into the container as root (best-effort)."""
    d = f"{_SKILL_ROOT}/{name}"
    dexec_root("mkdir", "-p", d)
    md = (
        "---\n"
        f"name: {name}\n"
        "description: mock fixture skill for the loader integration test\n"
        "version: 1.0.0\n"
        "---\n"
        f"# {name}\n\nWhen asked, reply with the marker {marker}.\n"
    )
    # write via a shell heredoc so quoting stays simple
    dexec_root("sh", "-c", f"cat > {d}/SKILL.md <<'EOF'\n{md}EOF")
    return d


def test_skill_loader_mock(llm, comm):
    with Checker("filesystem skill loader (mock)") as c:
        name = f"mocktest-{c.run_id}"
        marker = f"SKILLMARK{c.run_id}"
        print(f"\n=== OmegaClaw: skill-loader mock (run-id {c.run_id}) ===", flush=True)

        c.step("install a fixture SKILL.md into the container skill root")
        _install_fixture_skill(name, marker)
        c.ok("fixture installed", name)

        c.step("send prompt; mock invokes use-skill then send")
        prompt = make_prompt(
            c.run_id,
            f"Read your loaded skill '{name}' with the use-skill tool, then tell me "
            "the marker it instructs you to reply with. One short reply is enough.",
        )
        llm.set_answer(
            prompt,
            act(("use-skill", name),
                ("send", f"The skill instructed me to reply with {marker}.")),
        )
        if not comm.send_message(prompt):
            c.fail("comm", "could not deliver prompt within 60s")
        c.ok("comm", f"run-id={c.run_id}")

        c.step("verify agent invoked (use-skill ...)")
        arg = wait_for_skill_call(c.run_id, "use-skill", timeout=30)
        if arg is None:
            c.fail("use-skill invoked", "no (use-skill ...) within timeout")
        c.ok("use-skill invoked", f"arg={arg[:60]!r}")

        c.step("verify the real skill body reached history (marker present)")
        # The marker lives ONLY in the fixture body; its presence proves get_skill_body
        # returned the real body into the agent's results, not just that we scripted send.
        matched = wait_for_history_keyword(c.run_id, [marker], timeout=30)
        if not matched:
            c.fail("skill body delivered", f"marker {marker} not found in history window")
        c.ok("skill body delivered", f"marker={marker}")

        c.done()
