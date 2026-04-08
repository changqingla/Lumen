from importlib import import_module

MODULE_TO_PACKAGE_HINTS = {
    "langchain_google_genai": "langchain-google-genai",
    "langchain_anthropic": "langchain-anthropic",
    "langchain_openai": "langchain-openai",
    "langchain_deepseek": "langchain-deepseek",
}


def _build_missing_dependency_hint(module_path: str, err: ImportError) -> str:
    """在模块导入失败时构造可执行的依赖安装提示。"""
    module_root = module_path.split(".", 1)[0]
    missing_module = getattr(err, "name", None) or module_root

    # 对已知集成优先使用 provider 包名提示，即使导入错误是由传递依赖
    # （如 `google`）触发。
    package_name = MODULE_TO_PACKAGE_HINTS.get(module_root)
    if package_name is None:
        package_name = MODULE_TO_PACKAGE_HINTS.get(missing_module, missing_module.replace("_", "-"))

    return f"Missing dependency '{missing_module}'. Install it with `uv add {package_name}` (or `pip install {package_name}`), then restart lumen."


def resolve_variable[T](
    variable_path: str,
    expected_type: type[T] | tuple[type, ...] | None = None,
) -> T:
    """按路径解析变量并可选做类型校验。

    参数：
        variable_path: 变量路径（如
            `"parent_package_name.sub_package_name.module_name:variable_name"`）。
        expected_type: 期望类型（或类型元组）；若提供则使用 `isinstance()`
            校验解析结果。

    返回：
        解析到的变量对象。

    异常：
        ImportError: 模块路径无效或属性不存在时抛出。
        ValueError: 解析结果未通过校验时抛出。
    """
    try:
        module_path, variable_name = variable_path.rsplit(":", 1)
    except ValueError as err:
        raise ImportError(f"{variable_path} doesn't look like a variable path. Example: parent_package_name.sub_package_name.module_name:variable_name") from err

    try:
        module = import_module(module_path)
    except ImportError as err:
        module_root = module_path.split(".", 1)[0]
        err_name = getattr(err, "name", None)
        if isinstance(err, ModuleNotFoundError) or err_name == module_root:
            hint = _build_missing_dependency_hint(module_path, err)
            raise ImportError(f"Could not import module {module_path}. {hint}") from err
        # 对非“缺失模块”类错误，保留原始 ImportError 信息。
        raise ImportError(f"Error importing module {module_path}: {err}") from err

    try:
        variable = getattr(module, variable_name)
    except AttributeError as err:
        raise ImportError(f"Module {module_path} does not define a {variable_name} attribute/class") from err

    # 类型校验
    if expected_type is not None:
        if not isinstance(variable, expected_type):
            type_name = expected_type.__name__ if isinstance(expected_type, type) else " or ".join(t.__name__ for t in expected_type)
            raise ValueError(f"{variable_path} is not an instance of {type_name}, got {type(variable).__name__}")

    return variable


def resolve_class[T](class_path: str, base_class: type[T] | None = None) -> type[T]:
    """按路径解析类，并可选校验其父类关系。

    参数：
        class_path: 类路径（如 `"langchain_openai:ChatOpenAI"`）。
        base_class: 期望父类；若提供则校验解析出的类是否为其子类。

    返回：
        解析得到的类对象。

    异常：
        ImportError: 模块路径无效或属性不存在时抛出。
        ValueError: 解析结果不是类，或不是 `base_class` 子类时抛出。
    """
    model_class = resolve_variable(class_path, expected_type=type)

    if not isinstance(model_class, type):
        raise ValueError(f"{class_path} is not a valid class")

    if base_class is not None and not issubclass(model_class, base_class):
        raise ValueError(f"{class_path} is not a subclass of {base_class.__name__}")

    return model_class
