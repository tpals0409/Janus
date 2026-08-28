const signedRelease = process.env.JANUS_SIGNED_RELEASE === "1";

module.exports = {
  appId: "dev.janus.ade",
  productName: "Janus",
  asar: true,
  directories: { output: "dist" },
  files: ["out/**/*", "package.json"],
  extraResources: [
    // MIT·BSD·ISC·Apache는 고지와 라이선스 전문을 배포물과 함께 제공할 것을 요구한다.
    // Vite가 의존성을 번들해 배포하므로 그 의무가 실제로 발생한다.
    { from: "THIRD-PARTY-NOTICES.md", to: "THIRD-PARTY-NOTICES.md" },
    { from: "../LICENSE", to: "LICENSE" },
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
