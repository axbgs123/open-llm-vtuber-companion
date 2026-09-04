# Third-party notices

This repository is a modified distribution of Open-LLM-VTuber. Different
parts of the repository have different license terms; the root `LICENSE` does
not replace the licenses listed below.

## Open-LLM-VTuber backend and repository code

The upstream backend and repository code are distributed under the MIT
License. See `LICENSE` and the upstream project:

https://github.com/Open-LLM-VTuber/Open-LLM-VTuber

## Open-LLM-VTuber Web frontend

The `frontend` Git submodule points to the compiled build from
Open-LLM-VTuber-Web. It is distributed under **Open-LLM-VTuber License 1.0**,
which is based on Apache License 2.0 with additional conditions. In
particular, non-commercial use and VTuber content creation are permitted,
while paid hosting, commercial redistribution or rebranding, and commercial
embedding or integration require a separate commercial license.

See `LICENSE-Frontend.md` and:

https://github.com/Open-LLM-VTuber/Open-LLM-VTuber-Web

## Live2D sample models

The sample models under `live2d-models/` and their preview images under
`avatars/` are owned and copyrighted by Live2D Inc. They are not covered by
the repository's MIT License. Use and redistribution are subject to the
Live2D Cubism Sample Data Terms of Use and Free Material License Agreement in
`LICENSE-Live2D.md`.

Required notice:

> This content uses sample data owned and copyrighted by Live2D Inc. The
> sample data are utilized in accordance with terms and conditions set by
> Live2D Inc. This content itself is created at the author's sole discretion.

## Hikari idle animation

`companion_assets/motions/hikari-idle.vrma` comes from Kiu-Q/hikari-archive
and is distributed under the MIT License. See
`companion_assets/motions/HIKARI_IDLE_LICENSE.md`.

## Mesh2Motion animation pack

`companion_assets/motions/human-addon-animations.glb` comes from
Mesh2Motion/mesh2motion-app and is dedicated to the public domain under CC0
1.0 Universal. See `companion_assets/motions/MESH2MOTION_LICENSE.md`.

## CosyVoice integration

Files under `integrations/cosyvoice/` integrate with the separately
downloaded FunAudioLLM/CosyVoice project. The upstream runtime and model files
are not committed to this repository; the installer downloads them locally.
Review their current license and model terms before redistribution or
deployment:

https://github.com/FunAudioLLM/CosyVoice

## User-provided content

Local VRM avatars, cloned/reference voices, model weights, chat history,
memory databases, backups, secrets, and machine-specific configuration are
excluded from version control. Contributors must only add avatars, voices,
motions, backgrounds, and other media that they have the right to publish,
and should include the corresponding license and attribution.
