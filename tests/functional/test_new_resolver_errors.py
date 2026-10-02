import pathlib
import sys

from tests.lib import (
    PipTestEnvironment,
    create_basic_sdist_for_package,
    create_basic_wheel_for_package,
    create_test_package_with_setup,
)
from tests.lib.wheel import make_wheel


def test_new_resolver_conflict_requirements_file(
    tmpdir: pathlib.Path, script: PipTestEnvironment
) -> None:
    create_basic_wheel_for_package(script, "base", "1.0")
    create_basic_wheel_for_package(script, "base", "2.0")
    create_basic_wheel_for_package(
        script,
        "pkga",
        "1.0",
        depends=["base==1.0"],
    )
    create_basic_wheel_for_package(
        script,
        "pkgb",
        "1.0",
        depends=["base==2.0"],
    )

    req_file = tmpdir.joinpath("requirements.txt")
    req_file.write_text("pkga\npkgb")

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "-r",
        req_file,
        expect_error=True,
    )

    message = "package versions have conflicting dependencies"
    assert message in result.stderr, str(result)


def test_new_resolver_conflict_constraints_file(
    tmpdir: pathlib.Path, script: PipTestEnvironment
) -> None:
    create_basic_wheel_for_package(script, "pkg", "1.0")

    constraints_file = tmpdir.joinpath("constraints.txt")
    constraints_file.write_text("pkg!=1.0")

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "-c",
        constraints_file,
        "pkg==1.0",
        expect_error=True,
    )

    assert "ResolutionImpossible" in result.stderr, str(result)

    message = "The user requested (constraint) pkg!=1.0"
    assert message in result.stdout, str(result)


def test_new_resolver_requires_python_error(script: PipTestEnvironment) -> None:
    compatible_python = f">={sys.version_info.major}.{sys.version_info.minor}"
    incompatible_python = f"<{sys.version_info.major}.{sys.version_info.minor}"

    pkga = create_test_package_with_setup(
        script,
        name="pkga",
        version="1.0",
        python_requires=compatible_python,
    )
    pkgb = create_test_package_with_setup(
        script,
        name="pkgb",
        version="1.0",
        python_requires=incompatible_python,
    )

    # This always fails because pkgb can never be satisfied.
    result = script.pip(
        "install", "--no-build-isolation", "--no-index", pkga, pkgb, expect_error=True
    )

    # The error message should mention the Requires-Python: value causing the
    # conflict, not the compatible one.
    assert incompatible_python in result.stderr, str(result)
    assert compatible_python not in result.stderr, str(result)


def test_new_resolver_checks_requires_python_before_dependencies(
    script: PipTestEnvironment,
) -> None:
    incompatible_python = f"<{sys.version_info.major}.{sys.version_info.minor}"

    pkg_dep = create_basic_wheel_for_package(
        script,
        name="pkg-dep",
        version="1",
    )
    create_basic_wheel_for_package(
        script,
        name="pkg-root",
        version="1",
        # Refer the dependency by URL to prioritise it as much as possible,
        # to test that Requires-Python is *still* inspected first.
        depends=[f"pkg-dep@{pathlib.Path(pkg_dep).as_uri()}"],
        requires_python=incompatible_python,
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "pkg-root",
        expect_error=True,
    )

    # Resolution should fail because of pkg-root's Requires-Python.
    # This is done before dependencies so pkg-dep should never be pulled.
    assert incompatible_python in result.stderr, str(result)
    # Setuptools produces wheels with normalized names.
    assert "pkg_dep" not in result.stderr, str(result)
    assert "pkg_dep" not in result.stdout, str(result)


def test_new_resolver_no_versions_available_hint(script: PipTestEnvironment) -> None:
    """
    Test hint that no package candidate is available at all,
    when ResolutionImpossible occurs.
    """
    wheel_house = script.scratch_path.joinpath("wheelhouse")
    wheel_house.mkdir()

    incompatible_dep_wheel = make_wheel(
        name="incompatible-dep",
        version="1.0.0",
        wheel_metadata_updates={"Tag": ["py3-none-fakeplat"]},
    )
    incompatible_dep_wheel.save_to(
        wheel_house.joinpath("incompatible_dep-1.0.0-py3-none-fakeplat.whl")
    )

    # Create multiple versions of a package that depend on the incompatible dependency
    requesting_pkg_v1 = make_wheel(
        name="requesting-pkg",
        version="1.0.0",
        metadata_updates={"Requires-Dist": ["incompatible-dep==1.0.0"]},
    )
    requesting_pkg_v1.save_to(
        wheel_house.joinpath("requesting_pkg-1.0.0-py2.py3-none-any.whl")
    )

    requesting_pkg_v2 = make_wheel(
        name="requesting-pkg",
        version="2.0.0",
        metadata_updates={"Requires-Dist": ["incompatible-dep==1.0.0"]},
    )
    requesting_pkg_v2.save_to(
        wheel_house.joinpath("requesting_pkg-2.0.0-py2.py3-none-any.whl")
    )

    # Attempt to install the requesting package
    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        str(wheel_house),
        "requesting-pkg",
        expect_error=True,
    )

    # Check that ResolutionImpossible error occurred
    assert "ResolutionImpossible" in result.stderr, str(result)

    # Check that the new hint message is present
    assert (
        "Additionally, some packages in these conflicts have no "
        "matching distributions available for your environment:\n"
        "    incompatible-dep\n" in result.stdout
    ), str(result)


def test_new_resolver_reports_only_binary_source_exclusion(
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "sdist-dep", "1.0.0")
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["sdist-dep==1.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        expect_error=True,
    )

    assert (
        "No matching binary distribution was found for sdist-dep; "
        "a source distribution matching this requirement was found, but source "
        "distributions are excluded by the current --only-binary setting."
        in result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_respects_requirement_version(
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "sdist-dep", "1.0.0")
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["sdist-dep==2.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        expect_error=True,
    )

    assert "No matching binary distribution was found for sdist-dep" not in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_ignores_unrelated_wheel(
    script: PipTestEnvironment,
) -> None:
    create_basic_wheel_for_package(script, "mixed-dep", "1.0.0")
    create_basic_sdist_for_package(script, "mixed-dep", "2.0.0")
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["mixed-dep==2.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        expect_error=True,
    )

    assert "No matching binary distribution was found for mixed-dep" in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_with_incompatible_wheel(
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "mixed-dep", "1.0.0")
    incompatible_wheel = make_wheel(
        name="mixed-dep",
        version="1.0.0",
        wheel_metadata_updates={"Tag": ["py3-none-fakeplat"]},
    )
    incompatible_wheel.save_to(
        script.scratch_path.joinpath("mixed_dep-1.0.0-py3-none-fakeplat.whl")
    )
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["mixed-dep==1.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        expect_error=True,
    )

    assert "No matching binary distribution was found for mixed-dep" in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_is_deduplicated_after_backtracking(
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "sdist-dep", "1.0.0")
    for version in ("1.0.0", "2.0.0"):
        create_basic_wheel_for_package(
            script,
            "requesting-pkg",
            version,
            depends=["sdist-dep==1.0.0"],
        )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        expect_error=True,
    )

    message = "No matching binary distribution was found for sdist-dep"
    output = result.stderr + result.stdout
    assert output.count(message) == 1, str(result)
    assert (
        "matching distributions available for your environment" not in output
    ), str(result)


def test_new_resolver_reports_package_specific_only_binary_exclusion(
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "sdist-dep", "1.0.0")
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["sdist-dep==1.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        "sdist-dep",
        "requesting-pkg",
        expect_error=True,
    )

    assert "No matching binary distribution was found for sdist-dep" in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_qualifies_conflicting_requirement(
    script: PipTestEnvironment,
) -> None:
    create_basic_wheel_for_package(script, "mixed-dep", "1.0.0")
    create_basic_sdist_for_package(script, "mixed-dep", "2.0.0")
    create_basic_wheel_for_package(
        script,
        "pkga",
        "1.0.0",
        depends=["mixed-dep>=2"],
    )
    create_basic_wheel_for_package(
        script,
        "pkgb",
        "1.0.0",
        depends=["mixed-dep<2"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "pkga",
        "pkgb",
        expect_error=True,
    )

    output = result.stderr + result.stdout
    assert "No matching binary distribution was found for mixed-dep>=2" in output
    assert "No matching binary distribution was found for mixed-dep;" not in output


def test_new_resolver_only_binary_hint_not_shown_for_version_conflict(
    script: PipTestEnvironment,
) -> None:
    create_basic_wheel_for_package(script, "base", "1.0")
    create_basic_wheel_for_package(script, "base", "2.0")
    create_basic_wheel_for_package(
        script,
        "pkga",
        "1.0",
        depends=["base==1.0"],
    )
    create_basic_wheel_for_package(
        script,
        "pkgb",
        "1.0",
        depends=["base==2.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "pkga",
        "pkgb",
        expect_error=True,
    )

    assert "No matching binary distribution was found" not in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_not_shown_with_explicit_candidate(
    script: PipTestEnvironment,
) -> None:
    explicit_wheel = create_basic_wheel_for_package(
        script,
        "mixed-dep",
        "1.0.0",
    )
    create_basic_sdist_for_package(script, "mixed-dep", "2.0.0")
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["mixed-dep==2.0.0"],
    )

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "requesting-pkg",
        f"mixed-dep @ {explicit_wheel.as_uri()}",
        expect_error=True,
    )

    assert "No matching binary distribution was found for mixed-dep" not in (
        result.stderr + result.stdout
    ), str(result)


def test_new_resolver_only_binary_hint_not_shown_for_link_constraint(
    tmpdir: pathlib.Path,
    script: PipTestEnvironment,
) -> None:
    create_basic_sdist_for_package(script, "mixed-dep", "2.0.0")
    constrained_wheel = create_basic_wheel_for_package(
        script,
        "mixed-dep",
        "1.0.0",
    )
    create_basic_wheel_for_package(
        script,
        "requesting-pkg",
        "1.0.0",
        depends=["mixed-dep==2.0.0"],
    )

    constraints_file = tmpdir.joinpath("constraints.txt")
    constraints_file.write_text(f"mixed-dep @ {constrained_wheel.as_uri()}\n")

    result = script.pip(
        "install",
        "--no-cache-dir",
        "--no-index",
        "--find-links",
        script.scratch_path,
        "--only-binary",
        ":all:",
        "-c",
        constraints_file,
        "requesting-pkg",
        expect_error=True,
    )

    assert "No matching binary distribution was found for mixed-dep" not in (
        result.stderr + result.stdout
    ), str(result)
