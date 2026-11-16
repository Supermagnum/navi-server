# Phase 8 server retirement helpers

Adoption measure scripts land via the sibling draft PR
(`scripts/phase8/measure-rkyv-adoption.py` on `chore/phase8-rkyv-adoption-measure`).

Destructive DocumentRoot / ZFS steps are **not** shipped in-repo. On the
preparing workstation they are:

- `/tmp/navi-rkyv-retire.sh` (sudo; requires gate file with `G5_OK`)
- `/tmp/navi-rkyv-retire-rollback.sh`
- `/tmp/navi-rkyv-retire-destroy-snapshot.sh`
- `/tmp/navi-rkyv-retire-verify.sh` (no sudo)
- `/tmp/navi-rkyv-stub-current.json`
- `/tmp/navi-rkyv-oldclient-mock.py` (local-only old-client tests)

See `docs/phase8-rkyv-server-retire.md`.
