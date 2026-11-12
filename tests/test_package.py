def test_package_is_importable() -> None:
    import sentinel

    assert sentinel.__name__ == "sentinel"
