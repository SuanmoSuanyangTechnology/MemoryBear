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


def map_field_names(data_dict):
    mapped_dict = {}
    has_name_field = False

    # 第一遍：检查是否有name相关字段
    for key, value in data_dict.items():
        if key in ['name', 'entity2.name', 'entity1.name']:
            has_name_field = True
            break

    print(f"字段检查: has_name_field = {has_name_field}")

    # 第二遍：根据规则映射和过滤字段
    for key, value in data_dict.items():
        if key == 'entity2.name' or key == 'entity2_name':
            # 将 entity2.name 映射为 name
            mapped_dict['name'] = value
            print(f"字段名映射: {key} -> name")
        elif key == 'entity1.name' or key == 'entity1_name':
            # 将 entity1.name 映射为 name
            mapped_dict['name'] = value
            print(f"字段名映射: {key} -> name")
        elif key == 'entity1.description':
            # 将 entity1.description 映射为 description
            mapped_dict['description'] = value
            print(f"字段名映射: {key} -> description")
        elif key == 'entity2.description':
            # 将 entity2.description 映射为 description
            mapped_dict['description'] = value
            print(f"字段名映射: {key} -> description")
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
                mapped_dict[key] = value
                print(f"字段保留: {key}")
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
            mapped_dict[key] = value
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

    for i in solved_data:
        neo4j_dict_data = {}
        update_databases = {}
        results = i['results']
        for data in results:
            resolved = data.get('resolved')
            if not resolved:
                print("跳过：resolved为None")
                continue

            try:
                change_list = resolved.get('change', [])
            except (AttributeError, TypeError):
                change_list = []

            if change_list == []:
                print("跳过：change_list为空")
                continue

            if change_list and len(change_list) > 0:
                change = change_list[0]
                print(f"change: {change}")
                field_data = change.get('field', [])
                print(f"field_data: {field_data}")
                print(f"field_data type: {type(field_data)}")

                # 字段名映射和过滤函数


                # 处理field数据，可能是字典或列表
                if isinstance(field_data, dict):
                    # 如果是字典，映射字段名后更新
                    mapped_data = map_field_names(field_data)
                    update_databases.update(mapped_data)
                elif isinstance(field_data, list):
                    # 如果是列表，遍历每个字典并更新
                    for field_item in field_data:
                        if isinstance(field_item, dict):
                            mapped_item = map_field_names(field_item)
                            update_databases.update(mapped_item)
                        else:
                            print(f"警告: field_item不是字典: {field_item}")
                else:
                    print(f"警告: field_data类型不支持: {type(field_data)}")

            if 'entity1_name' in data:
                data['name'] = data.pop('entity1_name')
            if 'entity2_name' in data:
                data.pop('entity2_name', None)

            resolved_memory = resolved.get('resolved_memory', {})

            entity2 = None
            if isinstance(resolved_memory, dict):
                entity2 = resolved_memory.get('entity2')

            if entity2 and isinstance(entity2, dict) and len(entity2) >= 5:
                stat_id = resolved.get('original_memory_id')
                # 安全地获取description
                statement_id = None
                if isinstance(resolved_memory, dict):
                    statement_id = resolved_memory.get('statement_id')

                # 只有当neo4j_dict_data中还没有statement_id时才使用original_memory_id
                if statement_id and 'id' not in neo4j_dict_data:
                    neo4j_dict_data['id'] = stat_id
                    neo4j_dict_data['statement_id'] = statement_id
            else:
                # 处理original_memory_id，它可能是字符串或字典
                try:
                    for key, value in resolved_memory.items():
                        if key == 'statement_id':
                            neo4j_dict_data['statement_id'] = value
                        if key == 'description':
                            neo4j_dict_data['description'] = value
                except AttributeError:
                     neo4j_dict_data=[]

        print(neo4j_dict_data)
        print(update_databases)
        if neo4j_dict_data!=[]:
            await update_neo4j_data(neo4j_dict_data, update_databases)
        success_count += 1

    return success_count

