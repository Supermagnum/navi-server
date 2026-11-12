project(dump_mwm_roads)

set(SRC dump_mwm_roads.cpp)

omim_add_executable(${PROJECT_NAME} ${SRC})

target_link_libraries(${PROJECT_NAME}
  PRIVATE
    base
    coding
    geometry
    indexer
    platform
    succinct
)
