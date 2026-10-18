# Validation and acceptance

The platform repository's pull request workflow runs the configuration and
deployment-safety tests on GitHub Actions. These checks do not deploy to the
laptop's Kubernetes cluster or prove a live release.

Run the primary local checks from the repository root:

```bash
python3 -m unittest discover -s tests/config -v
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

Monitoring configuration and routing can be checked separately with:

```bash
bash monitoring/validate.sh
```

The optional incident adapter also has isolated unit and PostgreSQL integration
tests; its [README](../monitoring/incident-bridge/README.md) describes how to run
them without connecting to a real ticketing system.

Before treating the local lab as a production release exercise, also verify the
target-host workflow in [the deployment guide](deploy-cicd-kind.md): build and
publish the application through Jenkins, merge a GitOps PR, confirm Argo CD has
deployed the intended digest, run the full smoke journey, and exercise rollback
and database restore. Test real notification delivery only with an explicitly
configured receiver. Current test results are available from the relevant
GitHub Actions and Jenkins builds rather than a checked-in snapshot.

AWS remains a separate reference. Source validation does not prove a cloud
apply, live notification delivery, off-host backup, high availability or regional
recovery. See [SLOs](slo.md), [recovery targets](recovery-targets.md), and the
[disaster recovery runbook](runbooks/disaster-recovery.md) for those boundaries.
