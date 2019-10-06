from chinese_words_dao import *
from decorator import *
from pypinyin import Style
import pypinyin
import re
import jieba
from multiprocessing.pool import Pool
from multiprocessing import *
import logging
from itertools import *

# 这个模块主要存放对词组列表的一些操作。


def get_evaluation_level_tuple(word, word_dict, pattern):
    """
    获得对应元素的索引。
    :param word: 待转换成索引的词。
    :param word_dict: 从chinese_words.db取出的词典。
    :param pattern: 用于提取非中文字符中的正则预编译器，默认正则表达式是“^no_chinese:(.*?)$”。
    :return: 转换成包含词对应索引的元组。
    """
    # 对给定的字符串找寻对应的拼音
    def errors(x): return "".join(["no_chinese:", x])
    pinyin_list = pypinyin.pinyin(
        word,
        heteronym=False,
        style=Style.TONE3,
        errors=errors)

    # 对给定字符串中的每个字符建立相应的签名，并构建签名列表
    cur_index = -1        # 记录遍历word的位置
    signature_list = []        # 用于对照字典表中的索引
    for i in pinyin_list:
        pinyin = i[0]
        pinyin_match_list = pattern.findall(pinyin)
        if not pinyin_match_list:
            # 如果匹配到中文，连接发音成新的signature
            cur_index += 1
            signature = "".join([word[cur_index], "_", pinyin])
            signature_list.append(signature)
        else:
            # 如果匹配到非中文，直接装入signature_list
            signature_list += pinyin_match_list[0]
            cur_index += len(pinyin_match_list[0])

    # 对给定的字符串构建索引列表
    evaluation_level_list = []        # 字符的索引列表
    evaluation_level = 0        # 字符索引
    for signature in signature_list:
        try:
            evaluation_level = word_dict[signature]
        except KeyError:
            # 找不到索引进行相应处理
            logging.error("无法找到词语“%s”中的拼音索引为“%s”" % (word, signature))
        finally:
            evaluation_level_list.append(evaluation_level)

    return tuple(evaluation_level_list)


def handle_text_process(text, queue, process_id):
    """
    生产者进程：处理文本。
    :param text: 待分词的文本。
    :param queue: 进程间通信的队列。
    :param process_id: 进程id。
    :return: 文本分割后所有词的列表，所有词的最大长度。
    """
    max_length = 0        # 被分割的词的最大长度
    temp_length = 0       # 逐个计算“\n”前的词的长度
    temp_text_list = []        # 暂时存储一个词
    jieba.setLogLevel(20)       # 抑制jieba日志消息
    seged_word_list = list(jieba.cut(text))
    # 将分割后的词放入队列中
    word_set = set()        # 存储不重复的词的集合，用于过滤
    for word in seged_word_list:
        if word is not "\n":
            temp_text_list.extend(word)
        else:
            # 遇到“\n”结束标志，最后一并统计词的长度
            temp_length = len("".join(temp_text_list))
            max_length = temp_length if temp_length > max_length else max_length
            temp_text_list.clear()
        # 过滤分割后的不重复的词，防止进程间队列的通信阻塞
        if word not in word_set:
            word_set.add(word)
            queue.put(word)

    queue.put(None)  # 进程完成结束标志，向队列添加
    logging.info("分词进程%d已切割%d个不重复的词" % (process_id, len(word_set)))

    return seged_word_list, max_length


def get_filter_word_evaluation_process(queue_list):
    """
    消费者进程：获取一个集合中所有词对应在词典中的索引。
    :param queue_list: 装有队列的列表。
    :return:文本经分割后的不重复的词按词典查询所得的索引表。
    """
    filter_word_dict = {}     # 过滤后不重复的词映射表组成的索引词典
    word_dict = get_word_dict()  # 从数据库获得所有词的索引词典
    queue_count = len(queue_list)       # 队列数量
    word_list = [""] * queue_count      # 装从队列取出的词
    pattern = re.compile("^no_chinese:(.*?)$")  # 正则匹配英文字符串，取相应索引

    # 从队列中取元素
    while True:
        # 多个队列停止接收
        if word_list.count(None) == queue_count:
            logging.info("分词总结果为%d个不重复的词" % (len(filter_word_dict),))
            break
        for i in range(queue_count):
            if word_list[i] is not None:
                word = queue_list[i].get()
                word_list[i] = word
                # 如果从队列收集到的词不在索引词典中，则加入该词典
                if word is not None and word is not "\n" and word not in filter_word_dict.keys():
                    filter_word_dict[word] = get_evaluation_level_tuple(
                        word, word_dict, pattern)

    return filter_word_dict

# 多进程处理文本，text_list每个元素必须以"\n"结尾
@metric_time
def multiprocess_split_text_list(text_split_list):
    """
    多个生产者、一个消费者的多进程分割文本。
    :param text_split_list: 待处理分段的文本组成的列表。
    :return: 分割后的所有词的迭代对象，过滤后用于映射的词组的索引词典，所有词的最大长度。
    """
    n = len(text_split_list)        # 确定生产者的进程数
    process_result_list = []  # 收集进程运行的结果
    process_list = [handle_text_process] * n + \
                   [get_filter_word_evaluation_process]       # 待运行的进程
    queue_list = [Manager().Queue(maxsize=0)] * n       # 分多个队列，解决进程间通信时数据丢失的问题
    args_list = [(text_split_list[i], queue_list[i], i + 1)
                 for i in range(3)] + [(queue_list,)]       # 参数列表
    p = Pool(n + 1)       # 充分利用所有cpu
    for i in range(n + 1):
        a = p.apply_async(func=process_list[i], args=args_list[i])
        process_result_list.append(a)
    p.close()
    p.join()

    # 获得四个进程返回的结果
    seged_word_list_lists = [process_result_list[i].get()[0]
                             for i in range(n)]        # 存储三段文本分割出的词的列表
    max_length_list = [process_result_list[i].get()[1]
                       for i in range(n)]        # 存储文本中词的最大长度
    max_length = max(max_length_list)      # 通过比较得到文本中词的最大长度
    seged_word_iter = chain.from_iterable(
        seged_word_list_lists)  # 将三段文本分割的词组成一个可迭代对象（因为量大）
    # 不重复的词的索引词典及最大分割的词的长度
    filter_word_dict = process_result_list[n].get()

    return seged_word_iter, filter_word_dict, max_length


@metric_time
def hadle_seged_text_word(seged_text_word_iter, max_length, filter_word_dict):
    """
    当词组列表数量庞大时，用多进程将词组列表组成的文本分割后的所有词（有很多重复的）映射为对应索引。
    :param seged_text_word_iter: 分割后的所有词的迭代对象。
    :param max_length: 所有词的最大长度。
    :param filter_word_dict: 过滤后用于映射的词组的索引词典。
    :return: 排序好的词组的迭代对象。
    """
    evaluation_level_temp_list = []        # 暂时装入一个词对应的索引，并按maxlength补0
    text_word_temp_list = []        # 暂时装入一个词的各部分，便于后续连接
    # evaluation_level_list存储元素的形式为”(词的整体评级结果,词)“，如“(1,2,"你好")”
    evaluation_level_list = []

    # 将文本分割后的词按过滤后不重复的词组成的索引词典，映射为对应索引
    for word in seged_text_word_iter:
        if word is "\n":
            text_word_temp = "".join(text_word_temp_list)        # 连接词的各部分分割的词
            lack_lengh = max_length - \
                len(text_word_temp)        # 在词的索引后补0的个数，用于排序
            evaluation_level_temp_list.extend([0] * lack_lengh)     # 补0
            evaluation_level_temp_list.append(text_word_temp)
            evaluation_level_list.append(tuple(evaluation_level_temp_list))
            text_word_temp_list.clear()
            evaluation_level_temp_list.clear()
        else:
            text_word_temp_list.append(word)
            evaluation_level = filter_word_dict[word]
            evaluation_level_temp_list.extend(evaluation_level)

    radix_sort(evaluation_level_list)       # 排序
    for i in evaluation_level_list:
        yield i[-1].strip("\n")         # 生成器返回迭代对象结果


@metric_time
def handle_text_word(text_list):
    """
    当词组列表整体数量较少时，用单进程时的词组列表的排序操作。
    :param text_list: 待排序的词组列表。
    :return: 排序好的词组的迭代对象。
    """
    evaluation_level_list = []        # 存放排序好的词的列表
    pattern = re.compile("^no_chinese:(.*?)$")  # 正则匹配英文字符串，取相应索引
    word_dict = get_word_dict()  # 从数据库取用于对照的索引词典
    max_length = len(max(text_list, key=len))       # 找到元素最大长度，用于补充0
    for word in text_list:
        evaluation_level_tuple = get_evaluation_level_tuple(
            word, word_dict, pattern)
        lack_length = max_length - len(word)       # 按max_length补充缺失的0
        temp_iter = chain.from_iterable(
            [evaluation_level_tuple, [0] * lack_length, (word,)])
        evaluation_level_list.append(tuple(temp_iter))
    radix_sort(evaluation_level_list)       # 排序
    for i in evaluation_level_list:
        yield i[-1].strip("\n")         # 生成器返回迭代对象结果


@metric_time
def radix_sort(data):
    """
    对索引列表的每个元素纵向采用python原生的timsort算法，横向采用基数排序的思想进行排序。
    :param data: 待排序的词组列表。
    :return: 排序好的词组列表。
    """
    length = len(data[0])
    # 基数排序思想，signature放在末尾，也为了方便tuple排序
    for i in range(length - 2, -1, -1):
        data.sort(key=lambda x: x[i])


@metric_time
def get_text_spit_list(text_list):
    """
    将词组列表进行分段。
    :param text_list: 待分段的词组列表。
    :return: 分段后的词组列表。
    """
    temp = ""
    text_split_list = []
    n = cpu_count() - 1        # 由cpu数决定文本分段的个数
    quotient, remainder = divmod(len(text_list), n)
    if n <= 2:
        logging.error("机器的cpu数为%d，无法处理大量文本的数据" % (n + 1,))
        text_split_list = None
    for i in range(n):
        first_index = i * quotient
        end_index = (i + 1) * quotient if i < n - 1 else None
        temp = "".join(text_list[first_index:end_index])
        text_split_list.append(temp)
    return text_split_list


@metric_time
def sort_text_list(text_list):
    """
    排序汉字词组的列表，形如["人","人民"]，每个词的末尾不加”\n“。
    :param text_list: 汉字词组的列表。
    :return: 排序完的汉字词组的列表。
    """
    reslut_text_iter = []  # 存储排序后的迭代结果
    text_list = ["".join([i, "\n"]) for i in text_list]  # 添加”\n“做分割标志
    # 根据词的集合的大小进行相应的多进程/单进程操作
    if len(text_list) <= 500000:
        # 数据量小于500000用单进程即可
        reslut_text_iter = handle_text_word(text_list)
    else:
        # 数据量大于500000用多进程
        text_split_text = get_text_spit_list(text_list)
        seged_text_word_iter, filter_word_dict, max_length = multiprocess_split_text_list(
            text_split_text)
        reslut_text_iter = hadle_seged_text_word(
            seged_text_word_iter, max_length, filter_word_dict)

    return reslut_text_iter

if __name__=="__main__":
    text_list = ["人群", "河水", "人", "河流", "WTO世贸组织"]
    result_text_list=list(sort_text_list(text_list))
    print(result_text_list)