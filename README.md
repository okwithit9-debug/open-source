# open-source

Free, open-source software under the [MIT License](LICENSE).

This repository is the public umbrella for three projects. Sara AI lives in this tree. The Posting Tool and Open Source Clipper are git submodules, each pinned to a commit on its default branch.

## Projects

| Name | Description | Folder |
|------|-------------|--------|
| The Posting Tool | Free, open-source social scheduling for short-form video. | [`the-posting-tool/`](the-posting-tool/) |
| Open Source Clipper | Open-source video clipping tool that turns a long video into short captioned clips. | [`open-source-clipper/`](open-source-clipper/) |
| Sara AI | Open-source home AI node: local LLM chat and ComfyUI images behind Open WebUI. | [`sara-ai/`](sara-ai/) |

## Clone

```bash
git clone --recurse-submodules https://github.com/okwithit9-debug/open-source.git
```

If the repo is already cloned without submodules:

```bash
git submodule update --init --recursive
```

## License

MIT. See [LICENSE](LICENSE).
