cmake_minimum_required(VERSION 3.11)
project(environments)
find_package(ament_cmake REQUIRED)


install(DIRECTORY .
DESTINATION share/${PROJECT_NAME}
)
ament_package()
