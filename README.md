# canfar-cutout-worker

Headless CANFAR cutout worker for CanDIAPL applications.

This repo is a compute-only worker. It reads a manifest written by an orchestrator such as CoolHiPS, runs the requested cutout tool, updates durable job files in place, and exits. It is intentionally not an HTTP service.

## Runtime contract

The worker is invoked as:

```bash
python -m cutout_worker run --manifest /arc/.../manifest.json
```

The manifest must include:

- `job_id`
- `job_name`
- `operation`
- `cutout_tool`
- `layer_mode`
- `job_paths`
- `dirs`
- `layer`
- `postprocess`
- `work_items`

`cutout_tool` is required even when only one tool is available. The v1 allowed value is:

- `cutout-fits`

## Harbor image

Default image target:

```text
images.canfar.net/candiapl/canfar-cutout-worker:latest
```

## GitHub Action secrets

Required secrets for the manual Harbor publish workflow:

- `HARBOR_URL`
- `HARBOR_PROJECT`
- `HARBOR_USERNAME`
- `HARBOR_PASSWORD`
- `HARBOR_REPOSITORY`
- `HARBOR_IMAGE_TAG`

Recommended values:

- `HARBOR_URL=images.canfar.net`
- `HARBOR_PROJECT=candiapl`
- `HARBOR_REPOSITORY=canfar-cutout-worker`
- `HARBOR_IMAGE_TAG=latest`
