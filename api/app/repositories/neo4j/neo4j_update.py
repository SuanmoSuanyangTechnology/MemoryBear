from app.repositories import Neo4jConnector

neo4j_connector = Neo4jConnector()

async def update_neo4j_data(neo4j_dict_data, update_databases):
    """
    Update Neo4j data based on query criteria and update parameters

    Args:
        neo4j_dict_data: find
        update_databases: update
    """
    try:
        # 构建WHERE条件
        where_conditions = []
        params = {}

        for key, value in neo4j_dict_data.items():
            if value is not None:
                param_name = f"param_{key}"
                where_conditions.append(f"e.{key} = ${param_name}")
                params[param_name] = value

        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

        # 构建SET条件
        set_conditions = []
        for key, value in update_databases.items():
            if value is not None:
                param_name = f"update_{key}"
                set_conditions.append(f"e.{key} = ${param_name}")
                params[param_name] = value

        set_clause = ", ".join(set_conditions)

        if not set_clause:
            print("警告: 没有需要更新的字段")
            return False

        # 构建Cypher查询
        cypher_query = f"""
        MATCH (e:ExtractedEntity)
        WHERE {where_clause}
        SET {set_clause}
        RETURN count(e) as updated_count, collect(e.name) as updated_names
        """

        print(f"\n执行Cypher查询: {cypher_query}")
        print(f"参数: {params}")

        # 执行更新
        result = await neo4j_connector.execute_query(cypher_query, **params)

        if result:
            updated_count = result[0].get('updated_count', 0)
            updated_names = result[0].get('updated_names', [])
            print(f"成功更新 {updated_count} 个节点")
            if updated_names:
                print(f"更新的实体名称: {updated_names}")
            return updated_count > 0
        else:
            return False

    except Exception as e:
        print(f"更新过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        return False

async def update_neo4j_data_edge(neo4j_dict_data, update_databases):
    """
    Update Neo4j data based on query criteria and update parameters

    Args:
        neo4j_dict_data: find
        update_databases: update
    """
    try:
        # 构建WHERE条件
        where_conditions = []
        params = {}

        for key, value in neo4j_dict_data.items():
            if value is not None:
                param_name = f"param_{key}"
                where_conditions.append(f"r.{key} = ${param_name}")
                params[param_name] = value

        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

        # 构建SET条件
        set_conditions = []
        for key, value in update_databases.items():
            if value is not None:
                param_name = f"update_{key}"
                set_conditions.append(f"r.{key} = ${param_name}")
                params[param_name] = value

        set_clause = ", ".join(set_conditions)

        if not set_clause:
            print("警告: 没有需要更新的字段")
            return False

        # 构建Cypher查询
        cypher_query = f"""
        MATCH (n)-[r]->(m)
        WHERE {where_clause}
        SET {set_clause}
        RETURN count(r) as updated_count, collect(type(r)) as relation_types
        """

        print(f"\n执行Cypher查询: {cypher_query}")
        print(f"参数: {params}")

        # 执行更新
        result = await neo4j_connector.execute_query(cypher_query, **params)

        if result:
            updated_count = result[0].get('updated_count', 0)
            updated_names = result[0].get('updated_names', [])
            print(f"成功更新 {updated_count} 个节点")
            if updated_names:
                print(f"更新的实体名称: {updated_names}")
            return updated_count > 0
        else:
            return False

    except Exception as e:
        print(f"更新过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        return False
def map_field_names(data_dict):
    mapped_dict = {}
    has_name_field = False

    # 辅助函数：提取值（如果是数组则取最后一个值，否则直接返回）
    def extract_value(value):
        if isinstance(value, list) and len(value) > 0:
            # 如果是数组 [old_value, new_value]，取新值（最后一个）
            return value[-1]
        return value

    # 第一遍：检查是否有name相关字段
    for key, value in data_dict.items():
        if key in ['name', 'entity2.name', 'entity1.name']:
            has_name_field = True
            break

    print(f"字段检查: has_name_field = {has_name_field}")

    # 第二遍：根据规则映射和过滤字段
    for key, value in data_dict.items():
        # 提取实际值（处理数组格式）
        actual_value = extract_value(value)
        
        if key == 'entity2.name' or key == 'entity2_name':
            # 将 entity2.name 映射为 name
            mapped_dict['name'] = actual_value
            print(f"字段名映射: {key} -> name (值: {value} -> {actual_value})")
        elif key == 'entity1.name' or key == 'entity1_name':
            # 将 entity1.name 映射为 name
            mapped_dict['name'] = actual_value
            print(f"字段名映射: {key} -> name (值: {value} -> {actual_value})")
        elif key == 'entity1.description':
            # 将 entity1.description 映射为 description
            mapped_dict['description'] = actual_value
            print(f"字段名映射: {key} -> description (值: {value} -> {actual_value})")
        elif key == 'entity2.description':
            # 将 entity2.description 映射为 description
            mapped_dict['description'] = actual_value
            print(f"字段名映射: {key} -> description (值: {value} -> {actual_value})")
        elif key == 'relationship_type':
            # 跳过relationship_type字段
            print(f"字段过滤: 跳过不需要的字段 '{key}'")
            continue
        elif key == 'entity1_name':
            if has_name_field:
                # 如果有name字段，跳过entity1_name
                print(f"字段过滤: 由于存在name字段，跳过 '{key}'")
                continue
            else:
                # 如果没有name字段，保留entity1_name
                mapped_dict[key] = actual_value
                print(f"字段保留: {key} (值: {value} -> {actual_value})")
        elif key == 'entity2_name':
            if has_name_field:
                # 如果有name字段，跳过entity2_name
                print(f"字段过滤: 由于存在name字段，跳过 '{key}'")
                continue
            else:
                # 即使没有name字段，也不使用entity2_name（根据需求）
                print(f"字段过滤: 跳过不推荐的字段 '{key}'")
                continue
        elif '.' not in key:
            # 不包含点号的其他字段直接保留
            mapped_dict[key] = actual_value
            if isinstance(value, list):
                print(f"字段保留: {key} (数组值: {value} -> {actual_value})")
            else:
                print(f"字段保留: {key}")
        else:
            # 其他包含点号的字段跳过并警告
            print(f"警告: 跳过不支持的嵌套字段 '{key}'")

    print(f"字段映射结果: {mapped_dict}")
    return mapped_dict
async def neo4j_data(solved_data):
    """
        Process the resolved data and update the Neo4j database
        Args:
            Solved_data: Solution Data List
        Returns:
            Int: Number of successfully updated records
    """
    success_count = 0

    ori_entity = {}
    updata_entity = {}
    ori_edge = {}
    updata_edge = {}
    ori_expired_at={}
    updat_expired_at={}
    for i in solved_data:
        databasets = i['data']
        for key, values in databasets.items():
            if str(values)=='NONE':
                continue
            if isinstance(values, list):
                if key == 'description':
                    ori_entity[key] = values[0]
                    updata_entity[key] = values[1]
                if key == 'entity2_name' or key == 'entity1_name':
                    key = 'name'
                    ori_entity[key] = values[0]
                    updata_entity[key] = values[1]
                    ori_expired_at[key] = values[0]
                if key == 'statement':
                    ori_edge[key] = values[0]
                    updata_edge[key] = values[1]
                if key=='expired_at':
                    updat_expired_at[key] = values[1]

            elif key == 'statement_id':
                ori_edge[key] = values
                updata_edge[key] = values

                ori_entity[key] = values
                updata_entity[key] = values

                ori_expired_at[key] = values


        print(ori_entity)
        print(updata_entity)
        print(100*'-')
        print(ori_edge)
        print(updata_edge)
        expired_at_ = updat_expired_at.get('expired_at', None)
        if expired_at_ is not None:
            await update_neo4j_data(ori_expired_at, updat_expired_at)
            success_count += 1
        if ori_entity != updata_entity:
            await update_neo4j_data(ori_entity, updata_entity)
            success_count += 1
        if ori_edge != updata_edge:
            await update_neo4j_data_edge(ori_edge, updata_edge)
            success_count += 1

    return success_count

