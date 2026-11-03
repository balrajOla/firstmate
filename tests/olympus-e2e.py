"""Observable process, transport, installation and terminal acceptance tests; no model calls."""
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import sqlite3
import struct
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("OLYMPUS_TEST_BIN", ROOT / "target/debug/olympus"))
DAEMON = BIN.with_name("olympusd")

class ProcessContract(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix="olympus-e2e-")
        self.home = Path(self.area.name) / "home"
        self.children = []
        self.logs = []
        self.launch()

    def launch(self):
        log = open(Path(self.area.name) / f"daemon-{len(self.children)}.log", "wb")
        self.logs.append(log)
        proc = subprocess.Popen([str(DAEMON), "--home", str(self.home)], stdout=log, stderr=log, env=getattr(self,"environment",None))
        self.children.append(proc)
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError("Daemon exited: " + Path(log.name).read_text())
            if (self.home / "operator.token").exists():
                try:
                    self.cli("status")
                    return proc
                except AssertionError:
                    pass
            time.sleep(.05)
        raise AssertionError("Daemon did not become ready")

    def cli(self, *args, token=None, ok=True, home=None):
        cmd = [str(BIN), "--home", str(home or self.home)]
        if token: cmd += ["--token-file", str(token)]
        run = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=20)
        if ok:
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout) if run.stdout.strip() else None
        self.assertNotEqual(run.returncode, 0, run.stdout)
        return run.stderr

    def request(self, action, token=None, **fields):
        return self.cli("request", json.dumps({"action": action, **fields}), token=token)

    def fixture(self):
        p = self.request("project.create", name="Importer", charter="Preserve data", aliases=["importer"])["project"]
        worker = self.request("agent.create", project=p, role="rust", harness="codex")
        reviewer = self.request("agent.create", project=p, role="quality", harness="claude")
        t = self.request("task.create", project=p, role="rust", title="Preserve invalid rows", requirements="Prove all rows retained")["task"]
        return p, worker, reviewer, t

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
        for child in self.children:
            for stream in [child.stdin, child.stdout, child.stderr]:
                if stream: stream.close()
        for log in self.logs: log.close()
        self.area.cleanup()

    def test_cli_start_detaches_session_and_survives_terminal_hangup_boundary(self):
        detached=Path(self.area.name)/"detached"
        self.cli("start",home=detached)
        pid=int((detached/"daemon.lock").read_text().strip())
        try:
            self.assertNotEqual(os.getsid(pid),os.getsid(0))
            self.assertEqual(self.cli("status",home=detached)["schema"],"olympus.v1")
        finally:
            self.cli("stop",home=detached)
            for _ in range(100):
                if not (detached/"endpoint.json").exists():break
                time.sleep(.02)
            self.assertFalse((detached/"endpoint.json").exists())

    def test_restart_recovers_idempotent_requests_and_rejects_second_daemon(self):
        request = {"action": "project.create", "name": "Durable", "charter": "Survive process death", "request_id": "same-key"}
        original = self.cli("request", json.dumps(request))
        other = subprocess.run([str(DAEMON), "--home", str(self.home)], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn("another coordinator", other.stderr)
        self.children[0].kill(); self.children[0].wait()
        self.launch()
        self.assertEqual(self.cli("request", json.dumps(request)), original)
        self.assertEqual(len(self.cli("status")["projects"]), 1)

    def test_scoped_worker_cannot_stop_coordinator_and_review_matches_version(self):
        _, worker, reviewer, task = self.fixture()
        self.cli("request", '{"action":"system.shutdown"}', token=worker["credential_file"], ok=False)
        self.request("task.claim", task=task, agent=worker["agent"])
        result = self.request("task.result", token=worker["credential_file"], task=task, generation=1, body="Full output and test evidence", code_revision="commit-a")
        self.cli("request", json.dumps({"action":"task.review","artifact":result["artifact"],"reviewer":reviewer["agent"],"code_revision":"wrong","passed":True,"evidence":"wrong revision"}), ok=False)
        self.request("task.review", token=reviewer["credential_file"], artifact=result["artifact"], code_revision="commit-a", passed=True, evidence="Unit, integration and user journey passed")
        self.assertEqual(self.cli("status")["completed"][0]["id"], task)
        full=self.request("response.get", response=self.cli("status")["responses"][1]["id"])
        self.assertIn("Full output", full["body"])

    def test_slow_synthetic_runtime_effect_does_not_block_broker_or_replay(self):
        project,worker,_,task=self.fixture()
        root=Path(self.area.name)/"fake-firstmate";scripts=root/"bin";scripts.mkdir(parents=True)
        fmhome=Path(self.area.name)/"fake-home";fmhome.mkdir()
        fakebin=Path(self.area.name)/"fakebin";fakebin.mkdir()
        harness=fakebin/"codex";harness.write_text("#!/bin/sh\necho 'synthetic fixture v1'\n");harness.chmod(0o755)
        (scripts/'fm-brief.sh').write_text('#!/bin/bash\nmkdir -p "$FM_HOME/data/$1"\nprintf \'{TASK}\\n{FIRSTMATE_SPEC}\\n\' > "$FM_HOME/data/$1/brief.md"\n')
        (scripts/"fm-spawn.sh").write_text('#!/bin/sh\necho call >> "$FM_HOME/effects"\nprintf \'%s\\n\' "$@" > "$FM_HOME/launch-arguments"\nsleep 2\necho synthetic-launch-receipt\n')
        (scripts/"fm-control.sh").write_text('#!/bin/sh\nexit 0\n')
        (scripts/'fm-fleet-snapshot.sh').write_text('#!/bin/sh\nprintf \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\\n\'\n')
        repository=Path(self.area.name)/"repository";repository.mkdir()
        db=sqlite3.connect(self.home/"olympus.db")
        try:
            with db:db.execute("UPDATE projects SET repository=? WHERE id=?",(str(repository),project))
        finally:db.close()
        self.children[0].terminate();self.children[0].wait()
        self.environment={**os.environ,"PATH":str(fakebin)+os.pathsep+os.environ["PATH"]};self.launch()
        self.request("task.claim",task=task,agent=worker["agent"])
        self.request("profile.register",profile="fixture",harness="codex",model="fixture-only",version="synthetic fixture v1",capabilities={"roles":["rust"],"complexities":["standard","complex"],"effort_by_complexity":{"standard":"medium","complex":"high"}})
        self.request("profile.verify",profile="fixture",evidence={"harness":"codex","version":"synthetic fixture v1","real_harness":True,"command":"Synthetic bridge fixture; not a live conformance claim","observed_result":"Simulated lifecycle precondition","checks":dict.fromkeys(["launch","message","interrupt","resume","stop","effort"],True)})
        self.request("system.configure",dispatch_enabled=True)
        self.cli("connect","--firstmate-root",str(root),"--firstmate-home",str(fmhome),"--enable-execution")
        operation=self.request("operation.prepare",task=task,profile="fixture",verb="launch")
        until=time.monotonic()+6
        while not (fmhome/"effects").exists() and time.monotonic()<until:time.sleep(.02)
        self.assertTrue((fmhome/"effects").exists())
        start=time.monotonic();self.cli("status");self.assertLess(time.monotonic()-start,1.5)
        until=time.monotonic()+6
        while time.monotonic()<until:
            records=self.request("operations.list")
            if records[0]["state"]=="confirmed":break
            time.sleep(.05)
        self.assertEqual(records[0]["state"],"confirmed")
        self.assertEqual(records[0]["id"],operation["operation"])
        self.assertEqual((fmhome/"effects").read_text().splitlines(),["call"])
        argv=(fmhome/"launch-arguments").read_text().splitlines()
        self.assertEqual(argv[argv.index("--effort")+1],"medium")
        self.assertEqual(argv[argv.index("--model")+1],"fixture-only")
        self.assertEqual(self.request("task.get",task=task)["task"]["state"],"running")
        # Execute the broker address handed to the worker, including installed
        # layouts where the wrapper and actual binary live in different folders.
        import re
        brief=next((fmhome/"data").glob("*/brief.md")).read_text()
        connection=json.loads(re.search(r"Connection: (\{[^\n]+?\})\.",brief).group(1))
        response=subprocess.run([connection["binary"],"--home",connection["home"],"--token-file",connection["credential_file"],"request",json.dumps({"action":"task.get","task":task})],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(response.stdout)["task"]["id"],task)

    def test_socket_authentication_and_concurrent_reads(self):
        bad = Path(self.area.name) / "bad.token"; bad.write_text("invalid")
        self.cli("status", token=bad, ok=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(lambda _: self.cli("status"), range(64)))
        self.assertTrue(all(s["schema"]=="olympus.v1" for s in states))

    def test_installed_portfolio_journey_new_related_amended_linked_and_reopened_work(self):
        # Scripted agents drive real CLI, daemon and durable contracts; no model output is claimed.
        first=self.cli("submit","Forecast lunar observatory visibility")
        second=self.cli("submit","Estimate orchard harvest yields")
        self.assertTrue(first["project_created"]);self.assertTrue(second["project_created"])
        self.assertNotEqual(first["project"],second["project"])
        repo=Path(self.area.name)/"observatory-repo";repo.mkdir()
        subprocess.run(["git","init",str(repo)],capture_output=True,check=True)
        self.assertTrue(self.cli("bind",first["project"],str(repo))["bound"])
        self.assertEqual(self.cli("submit","Forecast lunar observatory visibility")["task"],first["task"])
        worker=self.request("agent.create",project=first["project"],role="engineer")
        peer=self.request("agent.create",project=second["project"],role="engineer")
        reviewer=self.request("agent.create",project=first["project"],role="quality")
        message={"action":"message.send","recipient":peer["agent"],"task":first["task"],"thread":"shared-observations","body":"Scripted fixture: request shared weather contract"}
        self.cli("request",json.dumps(message),token=worker["credential_file"],ok=False)
        self.request("task.link",task=second["task"],depends_on=first["task"])
        sent=self.cli("request",json.dumps(message),token=worker["credential_file"])
        for state in ["delivered","read","acted"]:self.request("message.ack",token=peer["credential_file"],message=sent["message"],state=state)
        reply=self.request("message.send",token=peer["credential_file"],recipient=worker["agent"],task=second["task"],thread="shared-observations",body="Scripted fixture: preserve observation timestamp and units")
        self.assertIn("timestamp",self.request("message.get",token=worker["credential_file"],message=reply["message"])["body"])
        self.request("task.claim",task=first["task"],agent=worker["agent"])
        self.cli("bind",first["project"],str(repo),ok=False)
        changed=self.cli("submit","Preserve observation timestamps and handle missing records","--task",first["task"],"--expected-version","1")
        self.assertEqual(changed["version"],2)
        self.cli("submit","Stale overwritten contract","--task",first["task"],"--expected-version","1",ok=False)
        until=time.monotonic()+5
        while time.monotonic()<until:
            inbox=self.request("message.inbox",token=worker["credential_file"])
            if any(m["thread"].startswith("requirements-") for m in inbox):break
            time.sleep(.05)
        self.assertTrue(any(m["thread"].startswith("requirements-") and m["version"]==2 for m in inbox))
        self.request("task.ack_version",token=worker["credential_file"],task=first["task"],version=2,checkpoint="Scripted fixture read version two")
        art=self.request("task.result",token=worker["credential_file"],task=first["task"],generation=1,body="Scripted observatory output: timestamped visibility records",code_revision="fixture-observatory-v2")
        self.request("task.review",token=reviewer["credential_file"],artifact=art["artifact"],code_revision="fixture-observatory-v2",passed=True,evidence="Scripted fixture validates current revision; no vendor quality claim")
        self.assertTrue(self.cli("outcomes",first["project"])["outputs"])
        self.assertEqual(self.cli("outcomes",second["project"])["outputs"],[])
        self.request("project.state",project=first["project"],state="completed")
        self.children[0].kill();self.children[0].wait();self.launch()
        followup=self.cli("submit","Forecast lunar observatory visibility: add dusk observations")
        self.assertEqual(followup["project"],first["project"]);self.assertTrue(followup["reopened"])
        self.assertTrue(followup["context"]["knowledge"])
        self.assertEqual(self.request("task.get",task=first["task"])["task"]["state"],"accepted")
        self.assertEqual(self.request("task.get",task=second["task"])["dependencies"][0]["depends_on"],first["task"])

    def test_ambiguous_intake_refuses_to_rewrite_either_existing_project(self):
        a=self.request("project.create",name="Atlas",charter="Invoice reconciliation")
        b=self.request("project.create",name="Atlas Mobile",charter="Mobile invoicing")
        before=self.cli("status")
        self.cli("submit","Atlas Mobile invoice changes",ok=False)
        self.assertEqual(self.cli("status")["tasks"],before["tasks"])
        out=self.cli("submit","Atlas Mobile invoice changes","--project",b["project"])
        self.assertEqual(out["project"],b["project"]);self.assertNotEqual(out["project"],a["project"])

    def test_profile_learning_is_automatic_durable_and_scoped_after_rework(self):
        project,worker,reviewer,task=self.fixture()
        self.request("profile.register",profile="fixture-quality",harness="codex",model="synthetic-model",version="fixture-v1",capabilities={"roles":["rust"]})
        self.request("profile.verify",profile="fixture-quality",evidence={"harness":"codex","version":"fixture-v1","real_harness":True,"command":"Scripted contract only; no model call","observed_result":"Synthetic fixture","checks":dict.fromkeys(["launch","message","interrupt","resume","stop"],True)})
        self.request("system.configure",dispatch_enabled=True)
        self.request("task.claim",task=task,agent=worker["agent"])
        op=self.request("operation.prepare",task=task,profile="fixture-quality",verb="launch")
        self.request("operation.receipt",operation=op["operation"],state="confirmed",receipt="Scripted fixture, no runtime connected")
        for passed in [False,True]:
            art=self.request("task.result",token=worker["credential_file"],task=task,generation=1,body="Scripted candidate rows",code_revision="fixture-fixed" if passed else "fixture-bug")
            self.request("task.review",token=reviewer["credential_file"],artifact=art["artifact"],code_revision="fixture-fixed" if passed else "fixture-bug",passed=passed,evidence="Scripted rows retained" if passed else "Scripted missing row regression")
        self.children[0].kill();self.children[0].wait();self.launch()
        learning=self.request("learning.status",project=project)
        self.assertEqual(len(learning["outcomes"]),1)
        self.assertEqual(learning["outcomes"][0]["outcome"],"accepted_after_rework")
        self.assertEqual(learning["outcomes"][0]["failures"],1)
        self.assertEqual({x["kind"] for x in learning["lessons"]},{"review_rejected","accepted"})
        unrelated=self.request("project.create",name="Unrelated",charter="Separate domain")
        other=self.request("agent.create",project=unrelated["project"],role="engineer")
        self.cli("request",json.dumps({"action":"learning.status","project":project}),token=other["credential_file"],ok=False)

    def test_calendar_restarts_without_duplicate_jobs_and_cli_groups_project_outcomes(self):
        project,_,_,_=self.fixture()
        schedule=self.request("schedule.create",project=project,title="Daily count audit",requirements="Check source and imported row counts",role="rust",cadence="daily",time="09:00",timezone="Europe/London")
        db=sqlite3.connect(self.home/"olympus.db")
        try:
            with db:db.execute("UPDATE schedules SET next_run=? WHERE id=?",(int(time.time())-1,schedule["schedule"]))
        finally:db.close()
        until=time.monotonic()+5
        while time.monotonic()<until:
            jobs=self.cli("agenda","today")["jobs"]
            if any(j.get("schedule")==schedule["schedule"] for j in jobs):break
            time.sleep(.1)
        self.assertEqual(sum(j.get("schedule")==schedule["schedule"] for j in jobs),1)
        self.children[0].kill();self.children[0].wait();self.launch()
        self.assertEqual(sum(j.get("schedule")==schedule["schedule"] for j in self.cli("agenda","today")["jobs"]),1)
        self.assertEqual(self.cli("outcomes",project)["project"]["id"],project)
        first=self.cli("submit","Importer follow-up with CSV","--project",project,"--role","rust")
        second=self.cli("submit","Importer follow-up with CSV","--project",project,"--role","rust")
        self.assertEqual(first["task"],second["task"]);self.assertTrue(second["duplicate"])

    def test_scheduled_backup_is_verified_and_operator_can_inspect_it(self):
        until=time.monotonic()+5;backups=[]
        while time.monotonic()<until:
            backups=self.request("backups.list")
            if backups and backups[0]["state"]=="verified":break
            time.sleep(.1)
        self.assertTrue(backups)
        self.assertEqual(backups[0]["state"],"verified",backups)
        self.assertIn("olympus.db",self.cli("backup","verify",backups[0]["destination"])["files"])

    def test_user_service_definitions_preserve_home_path_and_restart_policy(self):
        import xml.etree.ElementTree as ET
        systemd=subprocess.run([str(BIN),"--home",str(self.home),"service","systemd"],capture_output=True,text=True,check=True).stdout
        self.assertIn("Restart=on-failure",systemd);self.assertIn(str(self.home),systemd);self.assertIn('Environment="PATH=',systemd)
        launchd=subprocess.run([str(BIN),"--home",str(self.home),"service","launchd"],capture_output=True,text=True,check=True).stdout
        ET.fromstring(launchd)
        self.assertIn("KeepAlive",launchd);self.assertIn("EnvironmentVariables",launchd)
        self.assertEqual(self.cli("status")["schema"],"olympus.v1")

    def test_messages_persist_across_death_with_distinct_acknowledgements(self):
        _,worker,reviewer,task=self.fixture()
        message=self.request("message.send",token=worker["credential_file"],recipient=reviewer["agent"],task=task,thread="design",body="Check importer contract")
        self.children[0].kill(); self.children[0].wait(); self.launch()
        inbox=self.request("message.inbox",token=reviewer["credential_file"])
        self.assertEqual(inbox[0]["id"],message["message"])
        for state in ["delivered","read","acted"]:
            self.request("message.ack",token=reviewer["credential_file"],message=message["message"],state=state)
        self.assertEqual(self.request("message.inbox",token=reviewer["credential_file"]),[])

    def test_backup_restore_into_new_home_revokes_old_agent_and_keeps_records(self):
        _,worker,_,task=self.fixture()
        self.request("task.claim",task=task,agent=worker["agent"])
        copy=Path(self.area.name)/"snapshot";restore=Path(self.area.name)/"restored"
        self.cli("backup","create",str(copy))
        self.cli("backup","verify",str(copy))
        self.cli("backup","restore",str(copy),str(restore))
        original=self.home;self.home=restore;self.launch()
        self.assertEqual(self.request("task.get",task=task)["task"]["state"],"unknown")
        self.cli("status",token=worker["credential_file"],ok=False)
        self.cli("backup","restore",str(copy),str(restore),ok=False)
        self.home=original

    def test_real_terminal_navigation_resize_and_detach_preserves_service(self):
        _,_,_,task=self.fixture()
        master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack("HHHH",40,130,0,0))
        proc=subprocess.Popen([str(BIN),"--home",str(self.home),"ui"],stdin=slave,stdout=slave,stderr=slave)
        self.children.append(proc);os.close(slave)
        def read_until(marker):
            output=b"";until=time.monotonic()+8
            while time.monotonic()<until:
                if select.select([master],[],[],.1)[0]:
                    try: output+=os.read(master,65536)
                    except OSError: break
                    if marker in output:return output
            self.fail(f"Terminal did not show {marker!r}: {output[-1000:]!r}")
        try:
            read_until(b"OLYMPUS / Live")
            os.write(master,b"2");read_until(b"PROJECT / STATUS / CHARTER")
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack("HHHH",28,80,0,0));proc.send_signal(signal.SIGWINCH)
            os.write(master,b"7");read_until(b"RESPONSES /")
            os.write(master,b"8");read_until(b"TODAY /")
            os.write(master,b"9");read_until(b"THIS WEEK /")
            os.write(master,b"0");read_until(b"RECURRING /")
            os.write(master,b"q")
            # Keep acting as a terminal reader while the child flushes its final frame.
            # macOS PTYs have smaller buffers than Linux and wait() alone can deadlock.
            until=time.monotonic()+5
            while proc.poll() is None and time.monotonic()<until:
                if select.select([master],[],[],.05)[0]:
                    try:os.read(master,65536)
                    except OSError:break
            self.assertEqual(proc.wait(timeout=1),0)
            self.assertEqual(self.request("task.get",task=task)["task"]["state"],"ready")
        finally:os.close(master)

    @unittest.skipUnless(os.environ.get("OLYMPUS_PI_CLI"), "Set OLYMPUS_PI_CLI for the pinned real Pi token-free extension guard")
    def test_real_pi_loads_extension_and_registers_commands_without_model_call(self):
        env={**os.environ,"PI_CODING_AGENT_DIR":str(Path(self.area.name)/"pi"),"OLYMPUS_HOME":str(self.home),"OLYMPUS_BIN":str(BIN)}
        proc=subprocess.Popen(["node",os.environ["OLYMPUS_PI_CLI"],"--mode","rpc","--no-session","-e",os.environ.get("OLYMPUS_PI_EXTENSION",str(ROOT/"packages/olympus-pi/index.ts"))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        self.children.append(proc)
        proc.stdin.write('{"id":"commands","type":"get_commands"}\n');proc.stdin.flush()
        deadline=time.monotonic()+15;received=[]
        while time.monotonic()<deadline:
            if select.select([proc.stdout],[],[],.2)[0]:
                line=proc.stdout.readline()
                if not line:break
                response=json.loads(line);received.append(response)
                if response.get("id")=="commands":
                    self.assertTrue(response.get("success"),response)
                    names=[x["name"] for x in response["data"]["commands"]]
                    self.assertIn("olympus",names);self.assertIn("olympus-auto",names);return
        self.fail(f"Pi did not register commands: {received}")

if __name__=="__main__":unittest.main(verbosity=2)
