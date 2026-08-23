const signedRelease = process.env.JANUS_SIGNED_RELEASE === "1";

module.exports = {
  appId: "dev.janus.ade",
  productName: "Janus",
  asar: true,
  directories: { output: "dist" },
  files: ["out/**/*", "package.json"],
  extraResources: [
    {
      from: "../janus_server",
      to: "janus_server",
      filter: [
        "pyproject.toml",
        "uv.lock",
        "janus_server/**/*",
        "!janus_server/runs/**/*",
        "!janus_server/workspace/**/*",
        "!**/__pycache__/**",
      ],
    },
    {
      from: "../qwen3.8mlx",
      to: "qwen3.8mlx",
      filter: ["pyproject.toml", "uv.lock"],
    },
  ],
  mac: {
    category: "public.app-category.developer-tools",
    icon: "build/icon.png",
    target: signedRelease ? ["dmg", "zip"] : ["dir"],
    identity: signedRelease ? undefined : null,
    hardenedRuntime: signedRelease,
    gatekeeperAssess: false,
    notarize: signedRelease,
  },
  dmg: { sign: signedRelease },
};
