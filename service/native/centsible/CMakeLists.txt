cmake_minimum_required(VERSION 3.22)
project(centsible_py LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

add_subdirectory(core)            # verbatim copy of Centsible's core/ (refresh with tools/build_native.py --sync)
pybind11_add_module(_centsible binding.cpp)
target_link_libraries(_centsible PRIVATE centsible_core)
target_include_directories(_centsible PRIVATE core/src)
if(MSVC)
    target_compile_options(_centsible PRIVATE /O2 /W3)
else()
    target_compile_options(_centsible PRIVATE -O2)
endif()
