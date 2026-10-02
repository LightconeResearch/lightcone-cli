{
  description = "Lightcone Research: lc and astra";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      lastRelease = "0.5.0rc5";
      nextVersion =
        let
          parts = builtins.match "(.*[^0-9])([0-9]+)" lastRelease;
        in
        "${builtins.elemAt parts 0}${toString (lib.toInt (builtins.elemAt parts 1) + 1)}";
      commit = if self ? rev then builtins.substring 0 9 self.rev else self.dirtyShortRev or "unknown";
      version = "${nextVersion}.dev0+g${commit}.nix";
      excludeNewer =
        let
          at = start: length: builtins.substring start length self.lastModifiedDate;
        in
        "${at 0 4}-${at 4 2}-${at 6 2}T${at 8 2}:${at 10 2}:${at 12 2}Z";
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        rec {
          default = pkgs.callPackage (
            {
              lib,
              python3Packages,
              runCommand,
              makeWrapper,
              uv,
              git,
            }:
            let
              wheel = python3Packages.buildPythonPackage {
                pname = "lightcone-cli";
                inherit version;
                pyproject = true;
                src = ./.;
                build-system = [
                  python3Packages.hatchling
                  python3Packages.hatch-vcs
                ];
                env.SETUPTOOLS_SCM_PRETEND_VERSION = version;
                dontCheckRuntimeDeps = true;
              };
            in
            runCommand "lightcone-${version}"
              {
                nativeBuildInputs = [ makeWrapper ];
                passthru = { inherit uv git; };
                meta.mainProgram = "lc";
              }
              ''
                mkdir -p $out/bin
                ln -s ${uv}/bin/* ${git}/bin/* $out/bin/
                whl=$(echo ${wheel.dist}/*.whl)
                for exe in lc astra git-annex git-annex-shell git-remote-annex git-remote-tor-annex; do
                  makeWrapper ${lib.getExe uv} $out/bin/$exe \
                    --prefix PATH : $out/bin \
                    --add-flags "tool run --quiet --exclude-newer ${excludeNewer} --from $whl $exe"
                done
              ''
          ) { };
          lc = default;
        }
      );

      nixosModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.programs.lightcone;
        in
        {
          options.programs.lightcone = {
            enable = lib.mkEnableOption "the Lightcone Research tools (lc, astra)";
            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
              defaultText = lib.literalExpression "lightcone.packages.\${system}.default";
              description = "The package providing lc, astra, git-annex, uv and git.";
            };
          };

          config = lib.mkIf cfg.enable {
            assertions = [
              {
                assertion = lib.versionAtLeast cfg.package.uv.version "0.12";
                message = "programs.lightcone needs uv 0.12 or later, got ${cfg.package.uv.version}.";
              }
            ];
            programs.nix-ld.enable = true;
            programs.git = {
              enable = true;
              package = lib.mkDefault cfg.package.git;
            };
            environment = {
              systemPackages = [ cfg.package ];
              etc."uv/uv.toml".text = lib.mkDefault ''
                python-preference = "only-managed"
              '';
            };
          };
        };
    };
}
