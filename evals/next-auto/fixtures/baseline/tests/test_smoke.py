def test_package_imports():
    import fixturepkg

    assert fixturepkg.__all__ == []
