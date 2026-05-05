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

`cutout_tool` is required even when only one tool is selected by the orchestrator. The currently supported values are:

- `astropy` — default, WCS-aware spatial cutouts for 2D images and 3D cubes
- `cutout-fits` — CLI-based FITS cutout extraction

The worker is intentionally multi-engine from day one. CoolHiPS chooses the engine in the job manifest, and the worker dispatches to the matching adapter inside the same image.

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

## Harbor publish workflow

The repo ships a manual `Build and Push to CANFAR Harbor` GitHub Actions workflow. It:

1. builds the image locally in CI
2. smoke-tests both `astropy` and `cutout-fits` using tiny synthetic FITS inputs
3. pushes the tagged image to Harbor

That workflow is the intended deployment path for the cutout worker image.
