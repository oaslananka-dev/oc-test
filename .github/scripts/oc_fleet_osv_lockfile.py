#!/usr/bin/env python3
"""Evidence-bound, local-only npm OSV patch replay.

This entrypoint never creates or merges a PR or changes the live dispatcher.
Only one official GitHub-reviewed advisory, one npm package and one exact
package-lock node with a patch-version fix may qualify. Npm scripts are off.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys

REPO = "oaslananka/adxl355"
LOCK = "node/package-lock.json"
MANIFEST = "node/package.json"
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PACKAGE = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$")
GHSA = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}")
CVE = re.compile(r"CVE-\d{4}-\d{4,}")
FINDING = re.compile(
    r"^Package '([^'\s]+)@([0-9]+\.[0-9]+\.[0-9]+)' "
    r"is vulnerable to '(CVE-\d{4}-\d{4,})' "
    r"\(also known as '(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})'\)\.$"
)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def one_finding(sarif):
    require(type(sarif) is dict and len(sarif.get("runs", [])) == 1,
            "Require a single complete SARIF scan")
    results = sarif["runs"][0].get("results")
    require(type(results) is list and len(results) == 1, "Require exactly one vulnerability")
    entry = results[0]
    rule = entry.get("ruleId")
    text = (entry.get("message") or {}).get("text")
    require(isinstance(rule, str) and CVE.fullmatch(rule)
            and isinstance(text, str) and len(text) < 700,
            "Unsupported SARIF rule")
    match = FINDING.fullmatch(text)
    require(match is not None, "Unrecognized npm vulnerability evidence")
    pkg, old, cve, ghsa = match.groups()
    require(cve == rule and PACKAGE.fullmatch(pkg) is not None,
            "SARIF ID/package mismatch")
    locations = entry.get("locations")
    require(type(locations) is list and len(locations) == 1
            and locations[0].get("physicalLocation", {}).get(
                "artifactLocation", {}).get("uri") == LOCK,
            "Only the npm lockfile is in scope")
    return {"package":pkg,"old":old,"cve":cve,"ghsa":ghsa}


def validated_advisory(finding, advisory):
    require(type(advisory) is dict
            and advisory.get("ghsa_id") == finding["ghsa"]
            and advisory.get("type") == "reviewed"
            and {"type":"CVE","value":finding["cve"]} in advisory.get("identifiers",[])
            and advisory.get("withdrawn_at") is None
            and advisory.get("severity") in ("high","critical"),
            "Advisory not current, reviewed or severity eligible")
    vulnerabilities = advisory.get("vulnerabilities")
    require(type(vulnerabilities) is list and len(vulnerabilities) >= 1,
            "Incomplete official vulnerability records")
    matches = [v for v in vulnerabilities
               if v.get("package") == {"ecosystem":"npm","name":finding["package"]}]
    require(len(matches) == 1, "Official npm package does not match SARIF")
    fixed = matches[0].get("first_patched_version")
    require(isinstance(fixed, str) and VERSION.fullmatch(fixed),
            "No stable patch-level fix")
    o, f = tuple(map(int, finding["old"].split("."))), tuple(map(int, fixed.split(".")))
    require(o[:2] == f[:2] and f[2] > o[2],
            "Major/minor changes are not eligible for automatic patch replay")
    return fixed


def locked_package(lock, finding):
    require(type(lock) is dict and lock.get("lockfileVersion") == 3
            and type(lock.get("packages")) is dict, "Only npm lockfile v3 supported")
    key = "node_modules/" + finding["package"]
    info = lock["packages"].get(key)
    require(type(info) is dict and info.get("version") == finding["old"],
            "Live lockfile does not contain the reported vulnerable package/version")
    require(info.get("dev") is True, "Only dev dependency patches are eligible")
    return key


def validate_replay(before, after, finding, fixed):
    key = locked_package(before, finding)
    require(type(after) is dict and after.get("lockfileVersion") == 3
            and type(after.get("packages")) is dict,
            "Invalid updated lockfile")
    changed = set(before["packages"]) ^ set(after["packages"])
    require(not changed, "npm changed the dependency tree")
    for item in before["packages"]:
        if item != key:
            require(before["packages"][item] == after["packages"][item],
                    "Unexpected second dependency update")
    a, b = deepcopy(before), deepcopy(after)
    entry = b["packages"][key]
    require(entry.get("version") == fixed
            and entry.get("resolved") == (
                f"https://registry.npmjs.org/{finding['package']}/-/"
                f"{finding['package'].rsplit('/', 1)[-1]}-{fixed}.tgz")
            and isinstance(entry.get("integrity"),str)
            and entry["integrity"].startswith("sha512-"),
            "Patched npm resolution/integrity unexpected")
    for field in ("version","resolved","integrity"):
        a["packages"][key].pop(field, None)
        b["packages"][key].pop(field, None)
    require(a == b, "Lockfile changed unrelated metadata")
    return True


def gh_advisory(ghsa):
    require(GHSA.fullmatch(ghsa) is not None, "Invalid GHSA")
    output = subprocess.run(
        ["gh","api",f"/advisories/{ghsa}"],
        check=True,capture_output=True,text=True,timeout=25)
    return json.loads(output.stdout)


def prepare(root, sarif, advisory):
    root=Path(root)
    require(root.is_dir() and not root.is_symlink(), "Unsafe checkout path")
    require((root/LOCK).is_file() and not (root/LOCK).is_symlink(),
            "Expected real npm lockfile")
    require((root/MANIFEST).is_file() and not (root/MANIFEST).is_symlink(),
            "Expected real package manifest")
    require((root/".git").exists(), "Use an isolated Git checkout")
    status=subprocess.run(["git","-C",str(root),"status","--porcelain"],
                          check=True,capture_output=True,text=True,timeout=15)
    require(not status.stdout.strip(), "Checkout must be clean")
    finding=one_finding(sarif)
    fixed=validated_advisory(finding,advisory)
    before=json.loads((root/LOCK).read_text())
    locked_package(before,finding)
    return finding,fixed,before


def replay(root,sarif,advisory):
    root=Path(root)
    finding,fixed,before=prepare(root,sarif,advisory)
    # npm update accepts a validated package name as an argv element. Npm
    # may fetch registry metadata but never executes package lifecycle hooks.
    subprocess.run([
        "npm","update",finding["package"],"--package-lock-only",
        "--ignore-scripts","--no-audit","--no-fund"],
        cwd=root/"node",check=True,timeout=110,
        capture_output=True,text=True)
    after=json.loads((root/LOCK).read_text())
    validate_replay(before,after,finding,fixed)
    status=subprocess.run(["git","-C",str(root),"status","--porcelain"],
                          check=True,capture_output=True,text=True,timeout=15)
    require(status.stdout.strip() == "M node/package-lock.json",
            "Only the package lockfile may change")
    return {"repository":REPO,"ghsa":finding["ghsa"],"package":finding["package"],
            "from_version":finding["old"],"to_version":fixed,
            "changed_file":LOCK,"publish":False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--checkout",type=Path,required=True)
    p.add_argument("--sarif",type=Path,required=True)
    p.add_argument("--apply",action="store_true",help="Apply locally only; no GitHub writes")
    args=p.parse_args()
    require(args.sarif.is_file() and args.sarif.stat().st_size < 500_000,
            "No bounded SARIF evidence")
    sarif=json.loads(args.sarif.read_text())
    finding=one_finding(sarif)
    advisory=gh_advisory(finding["ghsa"])
    if args.apply:
        out=replay(args.checkout,sarif,advisory)
    else:
        finding,fixed,_=prepare(args.checkout,sarif,advisory)
        out={"repository":REPO,"ghsa":finding["ghsa"],"package":finding["package"],
             "from_version":finding["old"],"to_version":fixed,
             "changed_file":LOCK,"publish":False,"dry_run":True}
    print(json.dumps(out,indent=2,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError,OSError,subprocess.SubprocessError,KeyError,
            json.JSONDecodeError,TypeError) as error:
        print("OSV_LOCKFILE_BLOCKED",type(error).__name__,str(error)[:190],
              file=sys.stderr)
        sys.exit(1)
