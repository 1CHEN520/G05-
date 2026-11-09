def test_deliberately_failing():
    assert False, "这条用例故意失败，用来验证 CI 红灯能否挡住合并"
